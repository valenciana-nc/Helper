"""Regressions from real failed runs and the surrounding task/Qt lifecycle audit."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtWidgets import QApplication

import config
from engine_bridge import EngineRunner
from helper_engine.bootstrap import build_agent
from helper_engine.browser import BrowserDriver
from helper_engine.model import ProviderModel
from helper_engine.protocol import parse_decision
from helper_engine.schemas import VERIFICATION, decision_schema
from helper_engine.types import Action, Element, Predicate, ProtocolError, RunReport, RunStatus, StaleObservation, Target
from helper_engine.windows import WindowsDriver
from openai_client import ChatResult, OpenAIClient
from test_engine import FakeDriver, ScriptModel, act, agent, click, finish, frame
from test_engine_provider_contract import RecordingClient, RecordingCodex, observation


def response(**changes):
    data = {"kind": "act", "observation_id": "o1", "message": "Click Play.",
            "previous_outcome": "uncertain", "evidence": "", "milestones": [], "wait_ms": 200,
            "action": {"kind": "click", "target": {"element_id": "u1"}, "text": "", "keys": [],
                       "amount": 0, "destination": None, "expect": [], "requires_confirmation": False,
                       "description": "Click Play"}}
    return json.dumps({**data, **changes})


class ModelFormatRegressions(unittest.TestCase):
    def test_real_codex_transport_constrains_the_envelope_and_current_ids(self):
        codex = RecordingCodex(response(observation_id="obs-1"))
        model = ProviderModel(OpenAIClient(codex_provider=codex), "test")
        model.decide("Play the current video", observation(), {"mode": "active"})
        format_ = codex.bodies[0]["text"]["format"]
        self.assertEqual(format_["type"], "json_schema")
        self.assertTrue(format_["strict"])
        schema = format_["schema"]
        self.assertEqual(schema["properties"]["observation_id"]["enum"], ["obs-1"])
        action = schema["properties"]["action"]["anyOf"][0]
        target = action["properties"]["target"]["anyOf"][0]
        self.assertEqual(target["anyOf"][0]["properties"]["element_id"]["enum"], ["u1"])
        self.assertNotIn("type", schema["properties"])
        self.assertNotIn("target", schema["properties"])

    def test_custom_providers_keep_wire_compatibility_and_receive_full_schema(self):
        provider = Mock()
        provider.chat.return_value = ChatResult("{}")
        client = OpenAIClient(custom_provider=provider)
        client.chat([{"role": "user", "parts": [{"text": "Check"}]}], model="custom", response_schema=VERIFICATION)
        kwargs = provider.chat.call_args.kwargs
        self.assertNotIn("response_schema", kwargs)
        self.assertIn('"satisfied"', kwargs["system_prompt"])
        self.assertIn('"additionalProperties": false', kwargs["system_prompt"])

    def test_nullable_unused_action_fields_parse_without_weakening_semantic_checks(self):
        decision = parse_decision(response(action={"kind": "press", "target": None, "text": "", "keys": ["space"],
                                                    "amount": 0, "destination": None, "expect": [],
                                                    "description": "Play", "requires_confirmation": False}))
        self.assertEqual(decision.action.keys, ("space",))
        self.assertIsNone(decision.action.target)
        self.assertEqual(parse_decision(response(kind="answer", action=None, message="Hello")).kind, "answer")
        with self.assertRaises(ProtocolError):
            parse_decision(response(action={"kind": "click", "target": None}))
        with self.assertRaises(ProtocolError):
            parse_decision(response(kind="answer"))  # non-null action still forbidden

    def test_malformed_previous_outcome_is_a_repairable_protocol_error(self):
        for value in ([], {}, False, None):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                parse_decision(response(previous_outcome=value))

    def test_schema_is_fresh_for_each_observation(self):
        first = decision_schema(frame())
        first["properties"]["observation_id"]["enum"].append("wrong")
        self.assertEqual(decision_schema(frame("o2"))["properties"]["observation_id"]["enum"], ["o2"])

    def test_latest_failure_shape_repairs_then_executes_and_verifies(self):
        class Client(RecordingClient):
            def __init__(self):
                super().__init__("")
                self.replies = [
                    '{"kind":"click","observation_id":"o1","target":{"element_id":"u1"},"description":"Play"}',
                    response(), response(kind="finish", observation_id="o2", action=None, message="Playing now."),
                    '{"satisfied":true,"evidence":"Pause is visible.","missing":[]}',
                ]
            def chat(self, messages, **kwargs):
                super().chat(messages, **kwargs)
                return SimpleNamespace(text=self.replies.pop(0))
        client = Client()
        driver = FakeDriver([frame(), frame("o2"), frame("o3")])
        report = agent(ProviderModel(client, "test"), driver).run_goal("Play this video", mode="active")
        self.assertEqual(report.status, RunStatus.COMPLETED)
        self.assertEqual(len(driver.calls), 1)
        self.assertEqual(report.message, "Playing now.")
        repaired_payload = json.loads(client.messages[1][0]["parts"][0]["text"])
        self.assertIn("action.kind", repaired_payload["context"]["feedback"])
        self.assertIn("never at the top level", repaired_payload["context"]["feedback"])


class DispatchRegressions(unittest.TestCase):
    def test_escape_during_native_validation_prevents_any_dispatch(self):
        abort = threading.Event()
        driver = FakeDriver(validate=lambda *_: abort.set())
        report = agent(ScriptModel([act(click())]), driver).run_goal("Play", mode="active", should_abort=abort.is_set)
        self.assertEqual(report.status, RunStatus.CANCELLED)
        self.assertEqual(driver.calls, [])
        self.assertEqual(report.receipts, [])

    def test_cancellation_from_presentation_callback_also_prevents_dispatch(self):
        abort = threading.Event()
        driver = FakeDriver()
        def event(name, _payload):
            if name == "action_start":
                abort.set()
        report = agent(ScriptModel([act(click())]), driver).run_goal(
            "Play", mode="active", should_abort=abort.is_set, on_event=event)
        self.assertEqual(report.status, RunStatus.CANCELLED)
        self.assertEqual(driver.calls, [])

    def test_validation_exception_is_not_a_possible_click(self):
        def invalid(*_):
            raise RuntimeError("validation unavailable")
        driver = FakeDriver(validate=invalid)
        report = agent(ScriptModel([act(click())]), driver).run_goal("Play", mode="active")
        self.assertEqual(report.status, RunStatus.FAILED)
        self.assertEqual(report.metrics.actions, 0)
        self.assertEqual(report.receipts, [])
        self.assertIn("no input was sent", report.message)

    def test_stale_action_does_not_display_the_computer_use_frame(self):
        events = []
        def stale(*_):
            raise StaleObservation("scene moved")
        driver = FakeDriver(validate=stale)
        def question(obs):
            return parse_decision(response(kind="ask", observation_id=obs.id,
                                           message="The target is unavailable; please open it.", action=None))
        report = agent(ScriptModel([act(click()), question, question]), driver).run_goal(
            "Play", mode="active", on_event=lambda name, data: events.append((name, data)))
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)
        self.assertNotIn("action_start", [name for name, _ in events])
        self.assertTrue(any(data.get("reason") == "stale_target" for _, data in events))

    def test_native_validation_timeout_requires_a_fresh_lease(self):
        obs = replace(frame(), scope="42")
        uia = Mock()
        uia.request.side_effect = TimeoutError("native provider hung before input")
        driver = WindowsDriver(uia=uia)
        driver._current_id = obs.id
        with patch.object(driver, "_scope", return_value=42), self.assertRaises(StaleObservation):
            driver.validate(click(), obs)
        self.assertEqual(uia.request.call_args.args, ("validate",))

    def test_detached_dom_validation_is_not_reported_as_dispatched(self):
        page = Mock()
        page.is_closed.return_value = False
        page.url = "http://127.0.0.1/fixture"
        driver = BrowserDriver(page=page)
        driver._current_id = "o1"
        driver._url = page.url
        handle = Mock()
        handle.is_visible.side_effect = RuntimeError("node detached")
        driver._handles["u1"] = handle
        with self.assertRaises(StaleObservation):
            driver.validate(click(), frame())
        handle.click.assert_not_called()

    def test_postcondition_on_another_field_cannot_skip_this_fill(self):
        obs = frame(value="wrong")
        other = Element("u2", "Other", "Edit", obs.elements[0].rect, "Alice")
        obs = replace(obs, elements=(*obs.elements, other))
        action = Action("fill", Target("u1"), text="Alice", expect=(Predicate("value_equals", "Other", value="Alice"),))
        driver = FakeDriver([obs, replace(obs, id="o2"), replace(obs, id="o3")])
        report = agent(ScriptModel([act(action), finish]), driver).run_goal("Fill Name", mode="active")
        self.assertEqual(len(driver.calls), 1)
        self.assertEqual(report.metrics.actions, 1)


class RunnerLifecycleRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_optional_journal_open_failure_does_not_disable_the_engine(self):
        runner = EngineRunner()
        runner._agent = Mock()
        runner._agent.run_goal.return_value = RunReport("Hello", RunStatus.ANSWERED, "Hello")
        results = []
        runner.finished.connect(results.append)
        with patch("helper_engine.journal.JsonJournal", side_effect=OSError("read-only log folder")):
            runner._run("Hello", "active", False)
        runner._agent.run_goal.assert_called_once()
        self.assertEqual(results, [True])
        self.assertFalse(runner.is_running())

    def test_journal_write_and_close_failures_cannot_leave_the_app_busy(self):
        runner = EngineRunner()
        journal = Mock(side_effect=OSError("disk full"))
        journal.close.side_effect = OSError("close failed")
        class Agent:
            def run_goal(self, goal, *, on_event, **kwargs):
                on_event("started", {"mode": "active"})
                on_event("action_start", {"surface": "desktop", "kind": "open"})
                return RunReport(goal, RunStatus.COMPLETED, "Opened")
        runner._agent = Agent()
        runner._running = True
        results, presentation = [], []
        runner.finished.connect(results.append)
        runner.computer_use.connect(presentation.append)
        with patch("helper_engine.journal.JsonJournal", return_value=journal):
            runner._run("Open", "active", False)
        self.assertEqual(results, [True])
        self.assertEqual(presentation, [True, False])
        self.assertFalse(runner.is_running())

    def test_failed_thread_start_releases_busy_gate(self):
        runner = EngineRunner()
        changes = []
        runner.busy.connect(changes.append)
        with patch("engine_bridge.threading.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("cannot create thread")
            with self.assertRaises(RuntimeError):
                runner.start_goal("Hello")
        self.assertFalse(runner.is_running())
        self.assertEqual(changes, [True, False])


class DesktopLifecycleRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    @contextmanager
    def desktop(self):
        import main
        from conversation_store import ConversationStore
        with (tempfile.TemporaryDirectory() as folder,
              patch.object(main, "ensure_windows_shortcuts", return_value=[]),
              patch.object(main.HelplerDesktopApp, "_setup_tray"),
              patch.object(main.HelplerDesktopApp, "_initialize_backend")):
            app = main.HelplerDesktopApp(self.qt, "ctrl+shift+space", startup_surface="background")
            app._conversation_store = ConversationStore(Path(folder) / "conversations.json")
            app._dashboard._env_path = Path(folder) / ".env"
            try:
                yield app
            finally:
                app.shutdown()
                for widget in (app._widget, app._dashboard, app._status_pill):
                    widget.close()
                    widget.deleteLater()
                self.qt.processEvents()

    def test_second_submission_cannot_uncancel_an_inflight_run(self):
        with self.desktop() as app:
            app._engine_abort.set()
            app._busy = True
            with (patch.object(app._engine_runner, "is_running", return_value=True),
                  patch.object(app._engine_runner, "start_goal") as start):
                app._start_goal("Another task")
            start.assert_not_called()
            self.assertTrue(app._engine_abort.is_set())
            self.assertTrue(app._busy)
            self.assertIsNone(app._active_conversation)

    def test_transcription_crosses_to_qt_before_starting_a_goal(self):
        with self.desktop() as app:
            app._audio = Mock()
            app._audio.stop_recording.return_value = SimpleNamespace(duration_sec=1, wav_bytes=b"fixture")
            called_on = []
            with (patch("voice_bridge.transcribe_audio", return_value="Play my video"),
                  patch.object(app._engine_runner, "start_goal", side_effect=lambda *a, **k: called_on.append(
                      threading.get_ident()) or True)):
                worker = threading.Thread(target=app._finish_recording)
                worker.start()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(called_on, [])
                self.qt.processEvents()
                self.assertEqual(called_on, [threading.get_ident()])

    def test_escape_before_queued_transcript_arrives_prevents_new_run(self):
        with self.desktop() as app:
            app._busy = True
            app._engine_abort.set()
            with patch.object(app._engine_runner, "start_goal") as start:
                app._on_voice_transcribed("Play my video")
            start.assert_not_called()
            self.assertTrue(app._engine_abort.is_set())
            self.assertFalse(app._busy)

    def test_escape_stops_the_retained_speech_streamer(self):
        with self.desktop() as app:
            app._tts = Mock()
            app._on_escape_pressed()
            app._tts.stop.assert_called_once()
            self.assertTrue(app._engine_abort.is_set())

    def test_muted_orb_really_disables_voice_input_and_output(self):
        with self.desktop() as app:
            app._backend_ready = True
            app._audio = Mock()
            app._tts = Mock()
            app._widget.set_muted(True)
            app._start_voice_recording()
            app._speak_reply("Hello")
            app._audio.start_recording.assert_not_called()
            app._tts.speak.assert_not_called()
            app._tts.stop.assert_called_once()

    def test_late_target_events_cannot_restore_the_pointer_after_escape(self):
        with self.desktop() as app:
            app._engine_abort.set()
            with patch.object(app._ghost_cursor, "animate_to") as cursor:
                app._on_engine_highlight(1, 2, 30, 40, "Play")
            cursor.assert_not_called()

    def test_backend_not_ready_error_is_visible_without_opening_chat(self):
        with self.desktop() as app:
            app._backend_error = "input backend unavailable"
            app._handle_chat_submitted("Play")
            self.assertTrue(app._status_pill.isVisible())
            self.assertIn("input backend unavailable", app._status_pill.text())


    def test_restart_uses_saved_settings_not_inherited_old_dotenv_values(self):
        import main
        from env_io import write_env
        with self.desktop() as app:
            path = app._dashboard._env_path
            write_env(path, {"HELPER_AGENT_MODEL": "saved-model"})
            with (patch.object(main, "ENV_PATH", path),
                  patch.dict("os.environ", {"HELPER_AGENT_MODEL": "old-model"}),
                  patch.object(main.subprocess, "Popen") as launch,
                  patch.object(app, "_handle_quit_requested") as quit_):
                app._handle_restart_requested()
            self.assertEqual(launch.call_args.kwargs["env"]["HELPER_AGENT_MODEL"], "saved-model")
            quit_.assert_called_once()

    def test_invalid_or_failed_settings_save_does_not_restart(self):
        with self.desktop() as app:
            dashboard = app._dashboard
            restart = Mock()
            dashboard._restart_callback = restart
            with patch.object(dashboard, "_validate_settings", return_value="Invalid settings"):
                dashboard._save_and_restart()
            restart.assert_not_called()
            dashboard._original_values = {}
            with (patch.object(dashboard, "_validate_settings", return_value=None),
                  patch("ui_dashboard.write_env", side_effect=OSError("disk full"))):
                dashboard._save_and_restart()
            restart.assert_not_called()

    def test_nonfinite_form_values_and_unsafe_custom_url_are_rejected(self):
        with self.desktop() as app:
            dashboard = app._dashboard
            for key, value, label in (("HELPER_TEMPERATURE", "nan", "Temperature"),
                                      ("HELPER_MAX_TOKENS", "inf", "Max tokens")):
                with (patch.object(dashboard, "_current_provider_key", return_value="codex"),
                      patch.object(dashboard, "_field_value", side_effect=lambda k: value if k == key else "")):
                    self.assertIn(label, dashboard._validate_settings({}))
            dashboard._custom_api_base_input.setText("http://example.com/v1")
            dashboard._custom_api_key_input.setText("fixture-key")
            self.assertFalse(dashboard._save_custom_api())
            self.assertIn("HTTPS", dashboard._custom_api_status.text())

    def test_startup_animation_does_not_override_an_active_task(self):
        with self.desktop() as app:
            app._busy = True
            app._widget.set_state(app._widget.THINKING)
            app._finish_startup_animation()
            self.assertEqual(app._widget.state, app._widget.THINKING)
            app.shutdown()
            self.assertFalse(app._startup_timer.isActive())

    def test_speech_uses_configured_model_and_voice_without_joining_old_worker(self):
        import main
        with self.desktop() as app:
            app._audio = Mock()
            first, second = Mock(), Mock()
            with patch("helper_voice.TtsStreamer", side_effect=[first, second]) as factory:
                app._speak_reply("First")
                app._speak_reply("Second")
            factory.assert_called_with(model=main.TTS_MODEL, voice=main.TTS_VOICE)
            first.stop.assert_called_once()
            second.speak.assert_called_once_with(["Second"])
            self.assertIs(app._tts, second)


class ConfigurationRegressions(unittest.TestCase):
    def test_other_provider_models_are_not_replaced_with_a_codex_model(self):
        for provider, model in (("gemini", "gemini-2.5-flash"), ("anthropic", "claude-sonnet-4-6")):
            values = {"HELPER_PROVIDER": provider, "HELPER_AGENT_MODEL": model}
            self.assertEqual(config.resolve_provider_model("HELPER_AGENT_MODEL", values), model)
            self.assertEqual(config.resolve_provider_model("HELPER_AGENT_MODEL", {"HELPER_PROVIDER": provider}), model)

    def test_invalid_runtime_budget_settings_fall_back_before_agent_construction(self):
        with (patch.dict("os.environ", {"HELPER_RUN_TIMEOUT_SEC": "bad", "HELPER_GUIDE_WAIT_SEC": "nan"}),
              patch("helper_engine.model.ProviderModel"), patch("helper_engine.windows.WindowsDriver")):
            runtime = build_agent(backend="desktop")
        self.assertEqual(runtime.limits.max_seconds, config.AGENT_TIMEOUT_SEC)
        self.assertEqual(runtime.limits.help_wait_seconds, 90)


if __name__ == "__main__":
    unittest.main()
