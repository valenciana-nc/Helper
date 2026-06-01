"""Tests for rect_snap.snap_to_control and help_session.looks_oversized."""
from __future__ import annotations

import unittest
from unittest.mock import patch


class _FakeRect:
    __slots__ = ("left", "top", "right", "bottom")

    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _FakeElementInfo:
    def __init__(
        self,
        *,
        control_type: str = "",
        name: str = "",
        automation_id: str = "",
        rectangle: _FakeRect | None = None,
        handle: int | None = None,
        enabled: bool = True,
        visible: bool = True,
    ) -> None:
        self.control_type = control_type
        self.name = name
        self.automation_id = automation_id
        self.rectangle = rectangle
        self.handle = handle
        self.enabled = enabled
        self.visible = visible


class _FakeControl:
    def __init__(
        self,
        *,
        text: str = "",
        control_type: str = "",
        rect: _FakeRect | None = None,
        automation_id: str = "",
        handle: int | None = None,
        enabled: bool = True,
        visible: bool = True,
        children: list["_FakeControl"] | None = None,
    ) -> None:
        self._text = text
        self.handle = handle
        self._children = list(children or [])
        self.element_info = _FakeElementInfo(
            control_type=control_type,
            name=text,
            automation_id=automation_id,
            rectangle=rect,
            handle=handle,
            enabled=enabled,
            visible=visible,
        )

    def window_text(self) -> str:
        return self._text

    def children(self) -> list["_FakeControl"]:
        return list(self._children)

    def is_enabled(self) -> bool:
        return bool(self.element_info.enabled)

    def is_visible(self) -> bool:
        return bool(self.element_info.visible)


class _FakeDesktop:
    def __init__(self, toplevels: list[_FakeControl]) -> None:
        self._toplevels = list(toplevels)

    def windows(self, **_kwargs: object) -> list[_FakeControl]:
        return list(self._toplevels)


class _RecordingControl(_FakeControl):
    def __init__(self, *, visits: list[str], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._visits = visits

    def children(self) -> list[_FakeControl]:
        self._visits.append(self.window_text())
        return super().children()


def _make_button(
    name: str,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    control_type: str = "Button",
    automation_id: str = "",
) -> _FakeControl:
    return _FakeControl(
        text=name,
        control_type=control_type,
        rect=_FakeRect(x, y, x + w, y + h),
        automation_id=automation_id,
    )


def _make_window(
    name: str,
    x: int,
    y: int,
    w: int,
    h: int,
    children: list[_FakeControl],
    *,
    handle: int | None = None,
) -> _FakeControl:
    return _FakeControl(
        text=name,
        control_type="Window",
        rect=_FakeRect(x, y, x + w, y + h),
        handle=handle,
        children=children,
    )


class HelpIntentLanguageTests(unittest.TestCase):
    def test_file_action_aliases_expand_to_browse_language(self) -> None:
        from help_intents import tokenize_instruction

        tokens = tokenize_instruction("Upload a file")

        self.assertIn("browse", tokens)
        self.assertIn("choose", tokens)
        self.assertIn("attach", tokens)

    def test_copy_action_aliases_expand_to_duplicate_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        copy_tokens = tokenize_instruction("Copy this item")
        clone_tokens = tokenize_instruction("Clone this item")
        duplicate_tokens = tokenize_control("Duplicate")

        self.assertTrue({"clone", "copy", "duplicate"}.issubset(copy_tokens))
        self.assertTrue({"clone", "copy", "duplicate"}.issubset(clone_tokens))
        self.assertTrue({"clone", "copy", "duplicate"}.issubset(duplicate_tokens))

    def test_transfer_and_refresh_aliases_expand_to_matching_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("export", tokenize_instruction("Download the report"))
        self.assertIn("download", tokenize_control("Export"))
        self.assertIn("upload", tokenize_instruction("Import data"))
        self.assertIn("import", tokenize_control("Upload"))
        self.assertIn("reload", tokenize_instruction("Refresh the page"))
        self.assertIn("refresh", tokenize_control("Reload"))

    def test_send_action_aliases_expand_to_submit_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("submit", tokenize_instruction("Send the message"))
        self.assertIn("plane", tokenize_instruction("Send the message"))
        self.assertIn("send", tokenize_instruction("Submit the form"))
        self.assertIn("send", tokenize_control("Submit"))
        self.assertIn("submit", tokenize_control("Send"))
        self.assertIn("send", tokenize_control("Paper plane"))

    def test_meeting_control_aliases_expand_to_common_labels(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("mic", tokenize_instruction("Mute microphone"))
        self.assertIn("microphone", tokenize_instruction("Mute mic"))
        self.assertIn("video", tokenize_instruction("Start camera"))
        self.assertIn("camera", tokenize_instruction("Start video"))
        self.assertIn("webcam", tokenize_control("Camera"))
        self.assertIn("camera", tokenize_control("Webcam"))

    def test_audio_output_aliases_expand_to_speaker_language_contextually(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        mute_audio_tokens = tokenize_instruction("Mute audio")
        audio_settings_tokens = tokenize_instruction("Open audio settings")

        self.assertTrue({"speaker", "sound", "volume"}.issubset(mute_audio_tokens))
        self.assertNotIn("audio", mute_audio_tokens)
        self.assertNotIn("speaker", audio_settings_tokens)
        self.assertIn("sound", tokenize_instruction("Open volume"))
        self.assertIn("speaker", tokenize_control("Sound"))
        self.assertIn("volume", tokenize_control("Speaker"))

    def test_cart_action_aliases_expand_to_basket_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("basket", tokenize_instruction("Open cart"))
        self.assertIn("cart", tokenize_instruction("Open basket"))
        self.assertIn("bag", tokenize_instruction("Open cart"))
        self.assertIn("cart", tokenize_control("Basket"))
        self.assertIn("basket", tokenize_control("Shopping bag"))

    def test_password_visibility_intent_is_contextual(self) -> None:
        from help_intents import instruction_control_intents, tokenize_instruction, tokenize_control

        password_tokens = tokenize_instruction("Show password")
        sidebar_tokens = tokenize_instruction("Show sidebar")
        password_field_intents = instruction_control_intents("Focus password field")
        visibility_intents = instruction_control_intents("Show password")

        self.assertTrue({"eye", "visibility", "visible"}.issubset(password_tokens))
        self.assertNotIn("eye", sidebar_tokens)
        self.assertTrue({"button", "splitbutton"}.issubset(visibility_intents))
        self.assertNotIn("edit", visibility_intents)
        self.assertIn("edit", password_field_intents)
        self.assertIn("visibility", tokenize_control("Eye"))
        self.assertIn("eye", tokenize_control("Visibility"))

    def test_security_control_aliases_expand_to_lock_and_shield_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("padlock", tokenize_instruction("Lock screen"))
        self.assertIn("lock", tokenize_instruction("Click the padlock"))
        self.assertIn("lock", tokenize_instruction("Unlock account"))
        self.assertIn("shield", tokenize_instruction("Open security"))
        self.assertIn("security", tokenize_instruction("Click shield"))
        self.assertIn("lock", tokenize_control("Padlock"))
        self.assertIn("security", tokenize_control("Shield"))

    def test_navigation_and_time_aliases_expand_to_common_labels(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("date", tokenize_instruction("Open calendar"))
        self.assertIn("calendar", tokenize_instruction("Open date picker"))
        self.assertIn("time", tokenize_instruction("Open clock"))
        self.assertIn("clock", tokenize_instruction("Open time picker"))
        self.assertIn("house", tokenize_instruction("Go home"))
        self.assertIn("home", tokenize_instruction("Click the house"))
        self.assertIn("calendar", tokenize_control("Date"))
        self.assertIn("home", tokenize_control("House"))

    def test_print_action_aliases_expand_to_printer_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("printer", tokenize_instruction("Print document"))
        self.assertIn("print", tokenize_instruction("Open printer"))
        self.assertIn("printer", tokenize_control("Print"))
        self.assertIn("print", tokenize_control("Printer"))

    def test_folder_action_aliases_expand_to_directory_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("directory", tokenize_instruction("Open folder"))
        self.assertIn("folder", tokenize_instruction("Open directory"))
        self.assertIn("folder", tokenize_instruction("Open directories"))
        self.assertIn("directory", tokenize_control("Folder"))
        self.assertIn("folder", tokenize_control("Directory"))

    def test_save_action_aliases_expand_to_floppy_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("floppy", tokenize_instruction("Save document"))
        self.assertIn("save", tokenize_instruction("Click the floppy disk"))
        self.assertIn("save", tokenize_control("Floppy disk"))
        self.assertIn("floppy", tokenize_control("Save"))

    def test_favorite_action_aliases_expand_to_star_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        favorite_tokens = tokenize_instruction("Favorite this item")
        bookmark_tokens = tokenize_instruction("Bookmark this item")
        star_tokens = tokenize_control("Star")

        self.assertTrue({"bookmark", "favorite", "star"}.issubset(favorite_tokens))
        self.assertTrue({"bookmark", "favorite", "star"}.issubset(bookmark_tokens))
        self.assertTrue({"bookmark", "favorite", "star"}.issubset(star_tokens))

    def test_notification_action_aliases_expand_to_bell_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        bell_tokens = tokenize_instruction("Click the bell")
        notifications_tokens = tokenize_instruction("Open notifications")
        alerts_tokens = tokenize_instruction("Open alerts")
        control_tokens = tokenize_control("Bell")

        expected = {"alerts", "bell", "notification", "notifications"}
        self.assertTrue(expected.issubset(bell_tokens))
        self.assertTrue(expected.issubset(notifications_tokens))
        self.assertTrue(expected.issubset(alerts_tokens))
        self.assertTrue(expected.issubset(control_tokens))

    def test_symbol_only_control_text_yields_semantic_tokens(self) -> None:
        from help_intents import tokens_from_text

        cases = (
            ("?", {"help", "mark", "question"}),
            ("+", {"add", "create", "new", "plus"}),
            ("...", {"dot", "dots", "ellipsis", "menu", "more", "options"}),
            ("\u22ee", {"dot", "dots", "kebab", "menu", "more", "options"}),
            ("\u00d7", {"close", "dismiss", "x"}),
            ("\u2699", {"cog", "gear", "options", "preferences", "settings"}),
            ("\u2606", {"bookmark", "favorite", "star"}),
            ("\u2665", {"favorite", "heart"}),
            ("\U0001f514", {"alerts", "bell", "notification", "notifications", "notify"}),
            ("\U0001f3a4", {"mic", "microphone"}),
            ("\U0001f507", {"mute", "speaker", "sound", "volume"}),
            ("\U0001f50a", {"speaker", "sound", "volume"}),
            ("\U0001f4f7", {"camera", "video", "webcam"}),
            ("\U0001f6d2", {"bag", "basket", "cart"}),
            ("\U0001f441", {"eye", "visibility", "visible"}),
            ("\U0001f512", {"lock", "locked", "padlock"}),
            ("\U0001f513", {"lock", "padlock", "unlock", "unlocked"}),
            ("\U0001f6e1", {"secure", "security", "shield"}),
            ("\U0001f4c5", {"calendar", "date"}),
            ("\U0001f551", {"clock", "time"}),
            ("\U0001f3e0", {"home", "house"}),
            ("\U0001f5a8", {"print", "printer"}),
            ("\U0001f4c1", {"directory", "folder"}),
            ("\U0001f4be", {"disk", "floppy", "save"}),
            ("\U0001f50d", {"find", "lens", "magnifier", "magnifying", "search"}),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(tokens_from_text(text), expected)

    def test_picker_and_selector_intents_split_by_context(self) -> None:
        from help_intents import instruction_control_intents

        date_picker_intents = instruction_control_intents("Open the date picker")
        country_selector_intents = instruction_control_intents("Open the country selector")

        self.assertTrue(
            {"button", "splitbutton", "edit", "combobox"}.issubset(date_picker_intents)
        )
        self.assertEqual(country_selector_intents, {"combobox"})

    def test_iconic_disclosure_and_menu_launcher_intents(self) -> None:
        from help_intents import instruction_control_intents, menu_segment_intent

        chevron_intents = instruction_control_intents("Click the chevron")
        overflow_intents = instruction_control_intents("Open the overflow menu")
        profile_menu_intents = instruction_control_intents("Open the profile menu")
        account_dropdown_intents = instruction_control_intents("Open the account dropdown")
        menu_item_intents = instruction_control_intents("Open the file menu")
        explicit_item_intents = instruction_control_intents("Open the profile menu item")

        self.assertTrue({"button", "splitbutton"}.issubset(chevron_intents))
        self.assertEqual(overflow_intents, {"button", "splitbutton"})
        self.assertEqual(profile_menu_intents, {"button", "splitbutton"})
        self.assertTrue({"button", "splitbutton"}.issubset(account_dropdown_intents))
        self.assertTrue(menu_segment_intent(menu_item_intents))
        self.assertTrue(menu_segment_intent(explicit_item_intents))


class SnapToControlTests(unittest.TestCase):
    def test_snaps_to_nearby_button_with_matching_text(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Submit", 100, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (75, 195, 70, 35),
            "Click the Submit button",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 60, 30))
        self.assertIn("Submit", result.matched_text)
        self.assertGreaterEqual(result.confidence, 0.42)

    def test_returns_model_rect_when_no_overlap(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Other", 800, 800, 80, 30)
        window = _make_window("App", 700, 700, 200, 200, [button])
        desktop = _FakeDesktop([window])

        model_rect = (50, 50, 80, 30)
        result = snap_to_control(
            model_rect,
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")
        self.assertEqual(result.rect, model_rect)

    def test_text_match_breaks_ties(self) -> None:
        from rect_snap import snap_to_control

        wrong = _make_button("Cancel", 100, 200, 60, 30)
        right = _make_button("Submit", 110, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [wrong, right])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (105, 195, 60, 35),
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "uia")
        self.assertIn("Submit", result.matched_text)

    def test_skips_non_clickable_control_types(self) -> None:
        from rect_snap import snap_to_control

        text_label = _FakeControl(
            text="Submit",
            control_type="Text",
            rect=_FakeRect(100, 200, 160, 230),
        )
        window = _make_window("App", 0, 0, 800, 600, [text_label])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 60, 30),
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")

    def test_skips_disabled_or_hidden_controls(self) -> None:
        from rect_snap import snap_to_control

        disabled = _make_button("Submit", 100, 200, 60, 30)
        disabled.element_info.enabled = False
        hidden = _make_button("Submit", 110, 200, 60, 30)
        hidden.element_info.visible = False
        window = _make_window("App", 0, 0, 800, 600, [disabled, hidden])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 60, 30),
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")

    def test_semantic_mismatch_does_not_snap_wrong_labeled_control(self) -> None:
        from rect_snap import snap_to_control

        cancel = _make_button("Cancel", 100, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [cancel])
        desktop = _FakeDesktop([window])
        model_rect = (100, 200, 60, 30)

        result = snap_to_control(
            model_rect,
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_semantic_mismatch_rejects_loose_model_rect_centered_on_wrong_control(self) -> None:
        from rect_snap import snap_to_control

        cancel = _make_button("Cancel", 100, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [cancel])
        desktop = _FakeDesktop([window])
        model_rect = (80, 185, 120, 70)

        result = snap_to_control(
            model_rect,
            "Click Save",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 60, 30))
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_snap_rejects_visible_text_conflict_despite_matching_automation_id(self) -> None:
        from rect_snap import snap_to_control

        cancel = _make_button("Cancel", 100, 200, 60, 30, automation_id="saveButton")
        window = _make_window("App", 0, 0, 800, 600, [cancel])
        desktop = _FakeDesktop([window])
        model_rect = (100, 200, 60, 30)

        result = snap_to_control(
            model_rect,
            "Click Save",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")
        self.assertIn("saveButton", result.matched_text)

    def test_snap_prefers_visible_text_over_automation_only_match(self) -> None:
        from rect_snap import snap_to_control

        icon = _make_button("", 100, 200, 32, 32, automation_id="save_button")
        save = _make_button("Save", 145, 200, 80, 32)
        window = _make_window("App", 0, 0, 800, 600, [icon, save])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 32, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (145, 200, 80, 32))
        self.assertEqual(result.matched_text, "Save")
        self.assertFalse(result.rejected_reason)

    def test_snap_uses_camel_case_automation_id_when_no_visible_text_exists(self) -> None:
        from rect_snap import snap_to_control

        icon = _make_button("", 100, 200, 32, 32, automation_id="saveButton")
        window = _make_window("Editor", 0, 0, 800, 600, [icon])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 32, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 32, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_rejects_automation_only_match_when_visible_alternative_is_weak(self) -> None:
        from rect_snap import snap_to_control

        icon = _make_button("", 100, 200, 32, 32, automation_id="save_button")
        save = _make_button("Save", 190, 260, 80, 32, control_type="Spinner")
        window = _make_window("App", 0, 0, 800, 600, [icon, save])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 32, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 32, 32))
        self.assertEqual(result.rejected_reason, "automation-only target ambiguous")

    def test_snap_accepts_generic_checkbox_label_without_type_text(self) -> None:
        from rect_snap import snap_to_control

        checkbox = _make_button(
            "Enable precision mode",
            100,
            200,
            180,
            32,
            control_type="CheckBox",
        )
        window = _make_window("Settings", 0, 0, 800, 600, [checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 180, 32),
            "Click this checkbox.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 180, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_toggle_label_without_type_text(self) -> None:
        from rect_snap import snap_to_control

        checkbox = _make_button(
            "Dark mode",
            100,
            200,
            180,
            32,
            control_type="CheckBox",
        )
        window = _make_window("Settings", 0, 0, 800, 600, [checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 180, 32),
            "Click this toggle.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 180, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_switch_label_without_type_text(self) -> None:
        from rect_snap import snap_to_control

        checkbox = _make_button(
            "Dark mode",
            100,
            200,
            180,
            32,
            control_type="CheckBox",
        )
        window = _make_window("Settings", 0, 0, 800, 600, [checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 180, 32),
            "Click this switch.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 180, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_radio_option_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        option = _make_button(
            "Weekly",
            100,
            200,
            140,
            32,
            control_type="RadioButton",
        )
        window = _make_window("Schedule", 0, 0, 800, 600, [option])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 140, 32),
            "Select this option.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 140, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_slider_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        slider = _make_button(
            "Volume",
            100,
            200,
            220,
            32,
            control_type="Slider",
        )
        window = _make_window("Settings", 0, 0, 800, 600, [slider])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 220, 32),
            "Adjust this slider.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 220, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_spinner_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        spinner = _make_button(
            "History max tokens",
            100,
            200,
            160,
            32,
            control_type="Spinner",
        )
        window = _make_window("Settings", 0, 0, 800, 600, [spinner])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 160, 32),
            "Adjust this spinner.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 160, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_hyperlink_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        link = _make_button(
            "Documentation",
            100,
            200,
            140,
            28,
            control_type="Hyperlink",
        )
        window = _make_window("Help", 0, 0, 800, 600, [link])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 140, 28),
            "Click this hyperlink.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 140, 28))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_list_item_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        item = _make_button(
            "Settings",
            100,
            200,
            160,
            32,
            control_type="ListItem",
        )
        window = _make_window("App", 0, 0, 800, 600, [item])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 160, 32),
            "Click this list item.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 160, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_tree_item_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        item = _make_button(
            "Settings",
            100,
            200,
            160,
            32,
            control_type="TreeItem",
        )
        window = _make_window("App", 0, 0, 800, 600, [item])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 160, 32),
            "Click this tree item.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 160, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_compact_control_type_words_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("listitem", "ListItem", (100, 200, 160, 32)),
            ("treeitem", "TreeItem", (100, 200, 160, 32)),
            ("menuitem", "MenuItem", (100, 200, 160, 28)),
            ("tabitem", "TabItem", (100, 200, 140, 32)),
            ("headeritem", "HeaderItem", (100, 200, 140, 28)),
            ("splitbutton", "SplitButton", (100, 200, 160, 32)),
        )
        for word, control_type, rect in cases:
            with self.subTest(word=word):
                item = _make_button(
                    "Settings",
                    rect[0],
                    rect[1],
                    rect[2],
                    rect[3],
                    control_type=control_type,
                )
                window = _make_window("App", 0, 0, 800, 600, [item])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    f"Click this {word}.",
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_split_button_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        split = _make_button(
            "Export",
            100,
            200,
            160,
            32,
            control_type="SplitButton",
        )
        window = _make_window("App", 0, 0, 800, 600, [split])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 160, 32),
            "Click this split button.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 160, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_rejects_split_button_intent_on_plain_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button(
            "Export",
            100,
            200,
            160,
            32,
            control_type="Button",
        )
        window = _make_window("App", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 160, 32),
            "Click this split button.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 160, 32))
        self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_accepts_browser_address_bar_wording(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            "Focus the URL bar.",
            "Click the location bar.",
            "Click the omnibox.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                edit = _make_button(
                    "Address",
                    100,
                    200,
                    240,
                    32,
                    control_type="Edit",
                )
                window = _make_window("Browser", 0, 0, 800, 600, [edit])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (100, 200, 240, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (100, 200, 240, 32))
                self.assertFalse(result.rejected_reason)

    def test_snap_rejects_browser_address_bar_wording_on_plain_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button(
            "Address",
            100,
            200,
            240,
            32,
            control_type="Button",
        )
        window = _make_window("Browser", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 240, 32),
            "Focus the URL bar.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 240, 32))
        self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_rejects_text_entry_wording_on_plain_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Type your email.", "Email", (100, 200, 180, 32)),
            ("Enter the verification code.", "Verification code", (100, 200, 220, 32)),
            ("Click the search bar.", "Search", (100, 200, 180, 32)),
            ("Click the filter bar.", "Filter", (100, 200, 180, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                button = _make_button(label, *rect, control_type="Button")
                window = _make_window("App", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_keeps_explicit_enter_button_as_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Enter", 100, 200, 120, 32)
        window = _make_window("Dialog", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 120, 32),
            "Click the Enter button.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 120, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_rejects_state_and_choice_wording_on_plain_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Check Remember me.", "Remember me", (100, 200, 180, 32)),
            ("Uncheck Remember me.", "Remember me", (100, 200, 180, 32)),
            ("Tick Remember me.", "Remember me", (100, 200, 180, 32)),
            ("Turn on dark mode.", "Dark mode", (100, 200, 180, 32)),
            ("Enable notifications.", "Notifications", (100, 200, 180, 32)),
            ("Pick Daily choice.", "Daily", (100, 200, 180, 32)),
            ("Choose Weekly option.", "Weekly", (100, 200, 180, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                button = _make_button(label, *rect, control_type="Button")
                window = _make_window("App", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_keeps_check_for_updates_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Check for updates", 100, 200, 180, 32)
        window = _make_window("Settings", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 180, 32),
            "Check for updates.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 180, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_button_control_suffix(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Submit", 100, 200, 120, 32)
        window = _make_window("App", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 120, 32),
            "Click this button control.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 120, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_literal_edit_wording(self) -> None:
        from rect_snap import snap_to_control

        edit = _make_button(
            "Search",
            100,
            200,
            240,
            32,
            control_type="Edit",
        )
        window = _make_window("App", 0, 0, 800, 600, [edit])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 240, 32),
            "Click this edit control.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 240, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_contextual_control_container_wording(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Click this toolbar button.", "Save", "Button", (100, 200, 100, 32)),
            ("Click Toolbar button.", "Toolbar", "Button", (100, 200, 100, 32)),
            ("Click this toolbar icon.", "Settings", "Button", (100, 200, 32, 32)),
            ("Click this form field.", "Name", "Edit", (100, 200, 240, 32)),
            ("Click this dialog button.", "OK", "Button", (100, 200, 80, 32)),
            ("Click this modal button.", "OK", "Button", (100, 200, 80, 32)),
            ("Click this panel button.", "Save", "Button", (100, 200, 100, 32)),
            ("Click Panel button.", "Panel", "Button", (100, 200, 100, 32)),
            ("Click this table row.", "Order 123", "ListItem", (100, 200, 240, 32)),
            ("Click this grid row.", "Order 123", "ListItem", (100, 200, 240, 32)),
            ("Click this page link.", "Docs", "Hyperlink", (100, 200, 120, 28)),
            ("Click this card checkbox.", "Done", "CheckBox", (100, 200, 160, 32)),
            ("Click this section toggle.", "Dark mode", "CheckBox", (100, 200, 160, 32)),
            ("Click this drawer item.", "Settings", "ListItem", (100, 200, 160, 32)),
            ("Click this pane button.", "Apply", "Button", (100, 200, 100, 32)),
            ("Click this popup menu item.", "Open", "MenuItem", (100, 200, 120, 28)),
            ("Click this navigation tab.", "Settings", "TabItem", (100, 200, 140, 32)),
            ("Click this sidebar item.", "Settings", "ListItem", (100, 200, 160, 32)),
            ("Click this nav item.", "Settings", "ListItem", (100, 200, 160, 32)),
        )
        for instruction, label, control_type, rect in cases:
            with self.subTest(instruction=instruction):
                control = _make_button(
                    label,
                    rect[0],
                    rect[1],
                    rect[2],
                    rect[3],
                    control_type=control_type,
                )
                window = _make_window("App", 0, 0, 800, 600, [control])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertFalse(result.rejected_reason)

    def test_snap_allows_toggle_sidebar_button_label(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button(
            "Toggle sidebar",
            100,
            200,
            150,
            32,
            control_type="Button",
        )
        checkbox = _make_button(
            "Dark mode",
            100,
            250,
            150,
            32,
            control_type="CheckBox",
        )
        window = _make_window("App", 0, 0, 800, 600, [button, checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 150, 32),
            "Click Toggle sidebar.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 150, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_focus_field_intent_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        edit = _make_button(
            "Search",
            100,
            200,
            200,
            32,
            control_type="Edit",
        )
        window = _make_window("Search", 0, 0, 800, 600, [edit])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 200, 32),
            "Focus this field.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 200, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_column_header_intent_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        header = _make_button(
            "Status",
            100,
            50,
            120,
            28,
            control_type="HeaderItem",
        )
        window = _make_window("Grid", 0, 0, 800, 600, [header])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 50, 120, 28),
            "Click this column header.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 50, 120, 28))
        self.assertFalse(result.rejected_reason)

    def test_snap_rejects_checkbox_intent_on_plain_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("", 100, 200, 32, 32)
        window = _make_window("Settings", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 32, 32),
            "Click this checkbox.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 32, 32))
        self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_accepts_deictic_exact_labeled_control(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Save", 100, 200, 80, 32)
        window = _make_window("App", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 80, 32),
            "Click here.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 80, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_uses_single_checkbox_inside_loose_row(self) -> None:
        from rect_snap import snap_to_control

        checkbox = _make_button("Done", 24, 34, 20, 20, control_type="CheckBox")
        row = _make_button("Task row", 10, 10, 600, 80, control_type="ListItem")
        window = _make_window("Tasks", 0, 0, 800, 600, [row, checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (10, 10, 600, 80),
            "Click this checkbox.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (24, 34, 20, 20))
        self.assertFalse(result.rejected_reason)

    def test_snap_uses_single_checkbox_inside_contextual_row(self) -> None:
        from rect_snap import snap_to_control

        checkbox = _make_button("Done", 24, 34, 20, 20, control_type="CheckBox")
        row = _make_button("Task row", 10, 10, 600, 80, control_type="ListItem")
        window = _make_window("Tasks", 0, 0, 800, 600, [row, checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (10, 10, 600, 80),
            "Click the checkbox in Task row.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (24, 34, 20, 20))
        self.assertFalse(result.rejected_reason)

    def test_snap_prefers_splitbutton_menu_segment(self) -> None:
        from rect_snap import snap_to_control

        split = _make_button("Export", 100, 100, 180, 32, control_type="SplitButton")
        primary = _make_button("Export", 100, 100, 140, 32, control_type="Button")
        menu = _make_button("Export menu", 240, 100, 40, 32, control_type="MenuItem")
        window = _make_window("Toolbar", 0, 0, 800, 600, [split, primary, menu])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 100, 180, 32),
            "Open the Export menu.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (240, 100, 40, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_prefers_splitbutton_dropdown_segment(self) -> None:
        from rect_snap import snap_to_control

        split = _make_button("Export", 100, 100, 180, 32, control_type="SplitButton")
        primary = _make_button("Export", 100, 100, 140, 32, control_type="Button")
        menu = _make_button("Export menu", 240, 100, 40, 32, control_type="MenuItem")
        window = _make_window("Toolbar", 0, 0, 800, 600, [split, primary, menu])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 100, 180, 32),
            "Open the Export drop down.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (240, 100, 40, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_rejects_splitbutton_menu_without_precise_segment(self) -> None:
        from rect_snap import snap_to_control

        split = _make_button("Export", 100, 100, 180, 32, control_type="SplitButton")
        window = _make_window("Toolbar", 0, 0, 800, 600, [split])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 100, 180, 32),
            "Open the Export menu.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rejected_reason, "compound target ambiguous")

    def test_snap_accepts_menu_launcher_button_wording(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            "Open the overflow menu.",
            "Click the kebab menu.",
            "Click the three dots menu.",
            "Open the More options menu.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                button = _make_button("More options", 100, 200, 120, 32)
                window = _make_window("App", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (100, 200, 120, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (100, 200, 120, 32))
                self.assertFalse(result.rejected_reason)

    def test_snap_accepts_common_button_aliases(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Click Confirm.", "OK", (100, 200, 80, 32)),
            ("Click Previous.", "Back", (100, 200, 80, 32)),
            ("Click Continue.", "Next", (100, 200, 80, 32)),
            ("Click Sign in.", "Log in", (100, 200, 100, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                button = _make_button(label, *rect)
                window = _make_window("Dialog", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertFalse(result.rejected_reason)

    def test_snap_prefers_disclosure_button_inside_broad_row(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Click the chevron.", "Expand"),
            ("Click the down arrow.", "Expand"),
            ("Expand Advanced settings.", "Expand"),
            ("Collapse Advanced settings.", "Collapse"),
        )
        for instruction, button_text in cases:
            with self.subTest(instruction=instruction):
                row = _make_button(
                    "Advanced settings",
                    20,
                    80,
                    500,
                    80,
                    control_type="ListItem",
                )
                button = _make_button(button_text, 480, 104, 28, 28)
                window = _make_window("Settings", 0, 0, 800, 600, [row, button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (20, 80, 500, 80),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (480, 104, 28, 28))
                self.assertFalse(result.rejected_reason)

    def test_snap_rejects_selector_wording_on_plain_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            "Open the Country selector.",
            "Click the Country picker.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                button = _make_button("Country", 100, 200, 120, 32)
                window = _make_window("Form", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (100, 200, 120, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (100, 200, 120, 32))
                self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_keeps_explicit_picker_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Picker", 100, 200, 120, 32)
        window = _make_window("Form", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 120, 32),
            "Click the picker button.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 120, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_contextual_picker_launcher_buttons(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Open the Date picker.", "Date", (100, 200, 120, 32)),
            ("Click the Calendar picker.", "Calendar", (100, 200, 140, 32)),
            ("Open the Color picker.", "Color", (100, 200, 120, 32)),
            ("Click the File picker.", "Choose file", (100, 200, 140, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                button = _make_button(label, *rect)
                window = _make_window("Form", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertFalse(result.rejected_reason)

    def test_snap_accepts_file_action_alias_buttons(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Upload a file.", "Browse", (100, 200, 120, 32)),
            ("Open the file picker.", "Browse", (100, 200, 120, 32)),
            ("Choose a file.", "Browse", (100, 200, 120, 32)),
            ("Attach a document.", "Choose file", (100, 200, 140, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                button = _make_button(label, *rect)
                window = _make_window("Form", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertFalse(result.rejected_reason)

    def test_snap_rejects_multiple_checkboxes_inside_loose_row(self) -> None:
        from rect_snap import snap_to_control

        first = _make_button("Done", 24, 24, 20, 20, control_type="CheckBox")
        second = _make_button("Archived", 24, 52, 20, 20, control_type="CheckBox")
        row = _make_button("Task row", 10, 10, 600, 80, control_type="ListItem")
        window = _make_window("Tasks", 0, 0, 800, 600, [row, first, second])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (10, 10, 600, 80),
            "Click this checkbox.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_rejects_background_duplicate_when_foreground_is_plausible(self) -> None:
        from rect_snap import snap_to_control

        background_save = _make_button("Save", 100, 200, 80, 32)
        foreground_save = _make_button("Save", 100, 250, 80, 32)
        background = _make_window(
            "Background Editor",
            0,
            0,
            400,
            320,
            [background_save],
            handle=101,
        )
        foreground = _make_window(
            "Active Editor",
            0,
            40,
            400,
            320,
            [foreground_save],
            handle=202,
        )
        desktop = _FakeDesktop([background, foreground])

        result = snap_to_control(
            (100, 200, 80, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            foreground_handle_provider=lambda: 202,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 80, 32))
        self.assertEqual(result.rejected_reason, "foreground target ambiguous")

    def test_snap_rejects_occluded_background_target(self) -> None:
        from rect_snap import snap_to_control

        save = _make_button("Save", 100, 200, 80, 32)
        background = _make_window("Background Editor", 0, 0, 400, 320, [save], handle=101)
        blocking_dialog = _make_window("Blocking Dialog", 70, 170, 180, 100, [], handle=202)
        desktop = _FakeDesktop([background, blocking_dialog])

        def topmost_at(x: int, y: int) -> int:
            if 70 <= x < 250 and 170 <= y < 270:
                return 202
            return 101

        result = snap_to_control(
            (100, 200, 80, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            topmost_handle_provider=topmost_at,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 80, 32))
        self.assertEqual(result.matched_text, "Save")
        self.assertEqual(result.rejected_reason, "occluded target")

    def test_snap_rejects_own_process_target_instead_of_raw_fallback(self) -> None:
        from rect_snap import snap_to_control

        helper_button = _make_button("Save", 100, 200, 60, 30)
        helper_window = _make_window("Helper", 0, 0, 800, 600, [helper_button], handle=101)
        desktop = _FakeDesktop([helper_window])
        model_rect = (100, 200, 60, 30)

        with patch("rect_snap._is_own_process_window", side_effect=lambda hwnd: hwnd == 101):
            result = snap_to_control(
                model_rect,
                "Click Save",
                desktop_factory=lambda: desktop,
                timeout_ms=2000,
            )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.matched_text, "Save")
        self.assertEqual(result.rejected_reason, "own process target")

    def test_snap_uses_common_ui_label_synonyms(self) -> None:
        from rect_snap import snap_to_control

        options = _make_button("Options", 100, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [options])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 60, 30),
            "Click the settings gear.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 60, 30))
        self.assertIn("Options", result.matched_text)

    def test_factory_failure_falls_back_cleanly(self) -> None:
        from rect_snap import snap_to_control

        def boom() -> _FakeDesktop:
            raise RuntimeError("UIA unavailable")

        result = snap_to_control(
            (10, 10, 50, 50),
            "Click X",
            desktop_factory=boom,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")
        self.assertEqual(result.rect, (10, 10, 50, 50))

    def test_no_visible_windows_falls_back(self) -> None:
        from rect_snap import snap_to_control

        desktop = _FakeDesktop([])
        result = snap_to_control(
            (10, 10, 50, 50),
            "Click X",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")

    def test_descends_into_window_to_find_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Save", 410, 510, 50, 24)
        panel = _FakeControl(
            text="Toolbar",
            control_type="Pane",
            rect=_FakeRect(400, 500, 600, 540),
            children=[button],
        )
        window = _make_window("Editor", 0, 0, 1000, 800, [panel])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (405, 505, 60, 30),
            "Save the file",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (410, 510, 50, 24))


class ControlInventoryTests(unittest.TestCase):
    def _capture(self):
        from screen import Capture

        return Capture(
            png_bytes=b"png",
            width=800,
            height=600,
            monitor_left=0,
            monitor_top=0,
            scale=1.0,
        )

    def test_collects_clickable_visible_controls_with_stable_ids(self) -> None:
        from control_inventory import collect_control_candidates

        save = _make_button("Save", 100, 200, 60, 30, automation_id="save-btn")
        label = _FakeControl(
            text="Save",
            control_type="Text",
            rect=_FakeRect(100, 250, 160, 280),
        )
        disabled = _make_button("Disabled", 200, 200, 80, 30)
        disabled.element_info.enabled = False
        offscreen = _make_button("Offscreen", 900, 200, 80, 30)
        window = _make_window("Editor", 0, 0, 800, 600, [save, label, disabled, offscreen])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].id, "c001")
        self.assertEqual(candidates[0].text, "Save")
        self.assertEqual(candidates[0].automation_id, "save-btn")
        self.assertEqual(candidates[0].rect, (100, 200, 60, 30))

    def test_collect_dedupes_same_visible_control_with_different_automation_ids(self) -> None:
        from control_inventory import collect_control_candidates

        save_a = _make_button("Save", 100, 200, 60, 30, automation_id="save-a")
        save_b = _make_button("Save", 100, 200, 60, 30, automation_id="save-b")
        window = _make_window("Editor", 0, 0, 800, 600, [save_a, save_b])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].text, "Save")
        self.assertEqual(candidates[0].rect, (100, 200, 60, 30))

    def test_candidate_prompt_includes_target_ids_and_normalized_rects(self) -> None:
        from control_inventory import collect_control_candidates, format_candidates_for_prompt

        button = _make_button("Submit", 80, 120, 40, 30)
        window = _make_window("App", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])
        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        prompt = format_candidates_for_prompt(candidates, self._capture())

        self.assertIn("c001", prompt)
        self.assertIn("Submit", prompt)
        self.assertIn("norm=(100,200,50,50)", prompt)

    def test_candidate_prompt_separates_visible_text_from_automation_id(self) -> None:
        from control_inventory import ControlCandidate, format_candidates_for_prompt

        prompt = format_candidates_for_prompt(
            [
                ControlCandidate(
                    "c001",
                    "Cancel",
                    "button",
                    (10, 10, 60, 30),
                    automation_id="saveButton",
                )
            ],
            self._capture(),
        )

        self.assertIn('visible_text="Cancel"', prompt)
        self.assertIn('automation_id="saveButton"', prompt)
        self.assertNotIn('"Cancel saveButton"', prompt)
        self.assertIn("do not treat automation_id as visible screen text", prompt)

    def test_resolve_exact_target_id_wins_when_semantically_compatible(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        candidates = [
            ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30)),
            ControlCandidate("c002", "Submit", "button", (100, 10, 60, 30)),
        ]

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Submit.",
            candidates=candidates,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rect, (100, 10, 60, 30))
        self.assertFalse(result.rejected_reason)

    def test_target_id_semantic_mismatch_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Cancel.",
            candidates=[
                ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30)),
                ControlCandidate("c002", "Submit", "button", (100, 10, 60, 30)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id semantic mismatch")

    def test_target_id_accepts_deictic_exact_labeled_control(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click here.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (100, 100, 80, 32)),
            ],
            model_rect=(100, 100, 80, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (100, 100, 80, 32))

    def test_visible_text_conflict_rejects_target_id_despite_matching_automation_id(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Cancel",
                    "button",
                    (10, 10, 60, 30),
                    automation_id="saveButton",
                )
            ],
            model_rect=(10, 10, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id semantic mismatch")

    def test_visible_text_conflict_does_not_resolve_by_automation_id_text_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Cancel",
                    "button",
                    (10, 10, 60, 30),
                    automation_id="saveButton",
                )
            ],
        )

        self.assertIsNone(result)

    def test_text_match_prefers_visible_label_over_automation_only_geometry(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (120, 10, 80, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c002")
        self.assertFalse(result.rejected_reason)

    def test_target_id_accepts_common_ui_synonym_with_exact_geometry(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click the settings gear.",
            candidates=[
                ControlCandidate("c001", "Options", "button", (100, 10, 32, 32)),
            ],
            model_rect=(100, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (100, 10, 32, 32))

    def test_text_match_uses_common_ui_synonyms(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click the settings gear.",
            candidates=[
                ControlCandidate("c001", "Options", "button", (100, 10, 32, 32)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")

    def test_text_match_contextual_row_prefers_single_checkbox(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click the checkbox in Task row.",
            candidates=[
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 34, 20, 20)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c002")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (24, 34, 20, 20))

    def test_text_match_contextual_row_rejects_multiple_checkboxes(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click the checkbox in Task row.",
            candidates=[
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 24, 20, 20)),
                ControlCandidate("c003", "Archived", "checkbox", (24, 52, 20, 20)),
            ],
        )

        self.assertIsNone(result)

    def test_unlabeled_target_id_can_pass_with_exact_geometry_when_unambiguous(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this icon.",
            candidates=[
                ControlCandidate("c001", "", "button", (100, 10, 32, 32)),
            ],
            model_rect=(100, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "target_id")

    def test_unlabeled_target_id_rejects_exact_geometry_when_visible_alternative_matches(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32)),
                ControlCandidate("c002", "Save", "button", (100, 10, 60, 30)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_automation_only_target_id_rejects_when_visible_alternative_matches(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (120, 10, 80, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_target_id_duplicate_label_without_geometry_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30)),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_target_id_duplicate_label_with_geometry_is_accepted(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30)),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30)),
            ],
            model_rect=(298, 8, 64, 34),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (300, 10, 60, 30))

    def test_target_id_rejects_matching_row_when_tight_child_action_exists(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Settings.",
            candidates=[
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Settings", "button", (20, 20, 70, 30)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_generic_target_id_rejects_row_containing_tight_actions(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this button.",
            candidates=[
                ControlCandidate("c001", "Account row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Edit", "button", (450, 20, 60, 30)),
                ControlCandidate("c003", "Delete", "button", (520, 20, 70, 30)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_menu_target_id_rejects_broad_splitbutton_with_menu_segment(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Open the Export menu.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
            model_rect=(100, 100, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_text_match_prefers_splitbutton_menu_segment(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Open the Export menu.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
            model_rect=(100, 100, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c003")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (240, 100, 40, 32))

    def test_text_match_prefers_splitbutton_dropdown_segment(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Open the Export drop down.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
            model_rect=(100, 100, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c003")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (240, 100, 40, 32))

    def test_menu_launcher_target_id_accepts_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            "Open the overflow menu.",
            "Click the kebab menu.",
            "Click the three dots menu.",
            "Open the More options menu.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "More options", "button", (10, 10, 120, 32)),
                    ],
                    model_rect=(10, 10, 120, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_menu_launcher_text_match_accepts_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Open the overflow menu.",
            candidates=[
                ControlCandidate("c001", "More options", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_common_button_alias_target_ids_are_accepted(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Click Confirm.", "OK", "ok"),
            ("Click Previous.", "Back", "back"),
            ("Click Continue.", "Next", "next"),
            ("Click Sign in.", "Log in", "login"),
        )
        for instruction, label, candidate_id in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id=candidate_id,
                    instruction=instruction,
                    candidates=[
                        ControlCandidate(candidate_id, label, "button", (10, 10, 120, 32)),
                    ],
                    model_rect=(10, 10, 120, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.target_id, candidate_id)
                self.assertFalse(result.rejected_reason)

    def test_disclosure_target_id_inside_broad_row_accepts_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Click the chevron.", "Expand", "c002"),
            ("Click the down arrow.", "Expand", "c002"),
            ("Expand Advanced settings.", "Expand", "c002"),
            ("Collapse Advanced settings.", "Collapse", "c003"),
        )
        for instruction, label, candidate_id in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id=candidate_id,
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "Advanced settings", "listitem", (10, 10, 500, 80)),
                        ControlCandidate(candidate_id, label, "button", (468, 36, 28, 28)),
                    ],
                    model_rect=(10, 10, 500, 80),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.target_id, candidate_id)
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, (468, 36, 28, 28))

    def test_selector_target_id_rejects_plain_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Open the Country selector.",
            candidates=[
                ControlCandidate("c001", "Country", "combobox", (10, 10, 220, 32)),
                ControlCandidate("c002", "Country", "button", (280, 10, 100, 32)),
            ],
            model_rect=(280, 10, 100, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_selector_text_match_prefers_combobox_over_same_label_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            "Open the Country selector.",
            "Click the Country picker.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "Country", "combobox", (10, 10, 220, 32)),
                        ControlCandidate("c002", "Country", "button", (280, 10, 100, 32)),
                    ],
                    model_rect=(280, 10, 100, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "text_match")
                self.assertEqual(result.target_id, "c001")
                self.assertEqual(result.rect, (10, 10, 220, 32))
                self.assertFalse(result.rejected_reason)

    def test_selector_wording_keeps_explicit_picker_button_target_id(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click the picker button.",
            candidates=[
                ControlCandidate("c001", "Picker", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)

    def test_contextual_picker_launcher_target_ids_accept_buttons(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Open the Date picker.", "Date"),
            ("Click the Calendar picker.", "Calendar"),
            ("Open the Color picker.", "Color"),
            ("Click the File picker.", "Choose file"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (10, 10, 140, 32)),
                    ],
                    model_rect=(10, 10, 140, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)

    def test_file_action_alias_target_ids_accept_buttons(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Upload a file.", "Browse"),
            ("Open the file picker.", "Browse"),
            ("Choose a file.", "Browse"),
            ("Attach a document.", "Choose file"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (10, 10, 140, 32)),
                    ],
                    model_rect=(10, 10, 140, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)

    def test_generic_field_target_id_accepts_edit_containing_clear_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this field.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 600, 40)),
                ControlCandidate("c002", "Clear", "button", (570, 14, 28, 28)),
            ],
            model_rect=(10, 10, 600, 40),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 600, 40))

    def test_generic_field_target_id_rejects_wrong_button_type(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this field.",
            candidates=[
                ControlCandidate("c001", "Clear", "button", (570, 14, 28, 28)),
            ],
            model_rect=(570, 14, 28, 28),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_generic_checkbox_target_id_accepts_checkbox_label_without_type_text(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this checkbox.",
            candidates=[
                ControlCandidate("c001", "Enable precision mode", "checkbox", (10, 10, 200, 32)),
            ],
            model_rect=(10, 10, 200, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 200, 32))

    def test_generic_toggle_target_id_accepts_checkbox_label_without_type_text(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this toggle.",
            candidates=[
                ControlCandidate("c001", "Dark mode", "checkbox", (10, 10, 200, 32)),
            ],
            model_rect=(10, 10, 200, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 200, 32))

    def test_generic_switch_target_id_accepts_checkbox_label_without_type_text(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this switch.",
            candidates=[
                ControlCandidate("c001", "Dark mode", "checkbox", (10, 10, 200, 32)),
            ],
            model_rect=(10, 10, 200, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 200, 32))

    def test_generic_option_target_id_accepts_radio_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Select this option.",
            candidates=[
                ControlCandidate("c001", "Weekly", "radiobutton", (10, 10, 140, 32)),
            ],
            model_rect=(10, 10, 140, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 140, 32))

    def test_generic_option_target_id_accepts_listitem_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Select this option.",
            candidates=[
                ControlCandidate("c001", "Weekly", "listitem", (10, 10, 140, 32)),
            ],
            model_rect=(10, 10, 140, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 140, 32))

    def test_generic_slider_target_id_accepts_slider_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Adjust this slider.",
            candidates=[
                ControlCandidate("c001", "Volume", "slider", (10, 10, 220, 32)),
            ],
            model_rect=(10, 10, 220, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 220, 32))

    def test_generic_spinner_target_id_accepts_spinner_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Adjust this spinner.",
            candidates=[
                ControlCandidate("c001", "History max tokens", "spinner", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_generic_stepper_target_id_accepts_spinner_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this stepper.",
            candidates=[
                ControlCandidate("c001", "Retries", "spinner", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_generic_hyperlink_target_id_accepts_hyperlink_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this hyperlink.",
            candidates=[
                ControlCandidate("c001", "Documentation", "hyperlink", (10, 10, 140, 28)),
            ],
            model_rect=(10, 10, 140, 28),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 140, 28))

    def test_generic_list_item_target_id_accepts_listitem_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this list item.",
            candidates=[
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_generic_tree_item_target_id_accepts_treeitem_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this tree item.",
            candidates=[
                ControlCandidate("c001", "Settings", "treeitem", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_compact_control_type_target_ids_accept_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("listitem", "listitem", (10, 10, 160, 32)),
            ("treeitem", "treeitem", (10, 10, 160, 32)),
            ("menuitem", "menuitem", (10, 10, 160, 28)),
            ("tabitem", "tabitem", (10, 10, 140, 32)),
            ("headeritem", "headeritem", (10, 10, 140, 28)),
            ("splitbutton", "splitbutton", (10, 10, 160, 32)),
        )
        for word, control_type, rect in cases:
            with self.subTest(word=word):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=f"Click this {word}.",
                    candidates=[
                        ControlCandidate("c001", "Settings", control_type, rect),
                    ],
                    model_rect=rect,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, rect)

    def test_generic_split_button_target_id_accepts_splitbutton_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this split button.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_generic_split_button_target_id_rejects_plain_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this split button.",
            candidates=[
                ControlCandidate("c001", "Export", "button", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_browser_address_bar_target_id_accepts_edit(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            "Focus the URL bar.",
            "Click the location bar.",
            "Click the omnibox.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "Address", "edit", (10, 10, 240, 32)),
                    ],
                    model_rect=(10, 10, 240, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, (10, 10, 240, 32))

    def test_browser_address_bar_target_id_rejects_plain_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Focus the URL bar.",
            candidates=[
                ControlCandidate("c001", "Address", "button", (10, 10, 240, 32)),
            ],
            model_rect=(10, 10, 240, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_text_entry_action_target_id_rejects_plain_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Type your email.",
            candidates=[
                ControlCandidate("c001", "Email", "button", (10, 10, 180, 32)),
            ],
            model_rect=(10, 10, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_text_entry_action_text_match_prefers_edit_over_same_label_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Enter the verification code.",
            candidates=[
                ControlCandidate("c001", "Verification code", "edit", (10, 10, 260, 32)),
                ControlCandidate("c002", "Verification code", "button", (300, 10, 140, 32)),
            ],
            model_rect=(300, 10, 140, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")
        self.assertEqual(result.rect, (10, 10, 260, 32))
        self.assertFalse(result.rejected_reason)

    def test_text_entry_wording_keeps_explicit_enter_button_target_id(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click the Enter button.",
            candidates=[
                ControlCandidate("c001", "Enter", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_state_and_choice_target_id_rejects_plain_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Check Remember me.", "Remember me"),
            ("Turn on dark mode.", "Dark mode"),
            ("Enable notifications.", "Notifications"),
            ("Pick Daily choice.", "Daily"),
            ("Choose Weekly option.", "Weekly"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (10, 10, 180, 32)),
                    ],
                    model_rect=(10, 10, 180, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_state_action_text_match_prefers_checkbox_over_same_label_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Check Remember me.",
            candidates=[
                ControlCandidate("c001", "Remember me", "checkbox", (10, 10, 180, 32)),
                ControlCandidate("c002", "Remember me", "button", (240, 10, 120, 32)),
            ],
            model_rect=(240, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")
        self.assertEqual(result.rect, (10, 10, 180, 32))
        self.assertFalse(result.rejected_reason)

    def test_choice_wording_text_match_prefers_radio_over_same_label_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Pick Daily choice.",
            candidates=[
                ControlCandidate("c001", "Daily", "radiobutton", (10, 10, 180, 32)),
                ControlCandidate("c002", "Daily", "button", (240, 10, 120, 32)),
            ],
            model_rect=(240, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")
        self.assertEqual(result.rect, (10, 10, 180, 32))
        self.assertFalse(result.rejected_reason)

    def test_check_for_updates_keeps_button_target_id(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Check for updates.",
            candidates=[
                ControlCandidate("c001", "Check for updates", "button", (10, 10, 180, 32)),
            ],
            model_rect=(10, 10, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 180, 32))

    def test_button_control_suffix_target_id_accepts_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this button control.",
            candidates=[
                ControlCandidate("c001", "Submit", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_literal_edit_target_id_accepts_edit(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this edit control.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 240, 32)),
            ],
            model_rect=(10, 10, 240, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 240, 32))

    def test_contextual_container_target_id_accepts_exact_control(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Click this toolbar button.", "Save", "button", (10, 10, 100, 32)),
            ("Click Toolbar button.", "Toolbar", "button", (10, 10, 100, 32)),
            ("Click this toolbar icon.", "Settings", "button", (10, 10, 32, 32)),
            ("Click this form field.", "Name", "edit", (10, 10, 240, 32)),
            ("Click this dialog button.", "OK", "button", (10, 10, 80, 32)),
            ("Click this modal button.", "OK", "button", (10, 10, 80, 32)),
            ("Click this panel button.", "Save", "button", (10, 10, 100, 32)),
            ("Click Panel button.", "Panel", "button", (10, 10, 100, 32)),
            ("Click this table row.", "Order 123", "listitem", (10, 10, 240, 32)),
            ("Click this grid row.", "Order 123", "listitem", (10, 10, 240, 32)),
            ("Click this page link.", "Docs", "hyperlink", (10, 10, 120, 28)),
            ("Click this card checkbox.", "Done", "checkbox", (10, 10, 160, 32)),
            ("Click this section toggle.", "Dark mode", "checkbox", (10, 10, 160, 32)),
            ("Click this drawer item.", "Settings", "listitem", (10, 10, 160, 32)),
            ("Click this pane button.", "Apply", "button", (10, 10, 100, 32)),
            ("Click this popup menu item.", "Open", "menuitem", (10, 10, 120, 28)),
            ("Click this navigation tab.", "Settings", "tabitem", (10, 10, 140, 32)),
            ("Click this sidebar item.", "Settings", "listitem", (10, 10, 160, 32)),
            ("Click this nav item.", "Settings", "listitem", (10, 10, 160, 32)),
        )
        for instruction, label, control_type, rect in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, control_type, rect),
                    ],
                    model_rect=rect,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, rect)

    def test_switch_account_text_match_still_allows_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Switch account.",
            candidates=[
                ControlCandidate("c001", "Switch account", "button", (10, 10, 140, 32)),
                ControlCandidate("c002", "Dark mode", "checkbox", (10, 60, 140, 32)),
            ],
            model_rect=(10, 10, 140, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 140, 32))

    def test_toggle_sidebar_text_match_still_allows_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Toggle sidebar.",
            candidates=[
                ControlCandidate("c001", "Toggle sidebar", "button", (10, 10, 150, 32)),
                ControlCandidate("c002", "Dark mode", "checkbox", (10, 60, 140, 32)),
            ],
            model_rect=(10, 10, 150, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 150, 32))

    def test_generic_column_header_target_id_accepts_header_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this column header.",
            candidates=[
                ControlCandidate("c001", "Status", "headeritem", (100, 50, 120, 28)),
            ],
            model_rect=(100, 50, 120, 28),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (100, 50, 120, 28))

    def test_generic_checkbox_target_id_rejects_wrong_button_type(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this checkbox.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_target_id_foreground_duplicate_without_geometry_is_accepted(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.target_id, "c002")

    def test_target_id_background_duplicate_without_geometry_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_target_id_background_duplicate_with_geometry_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
            model_rect=(10, 10, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_text_match_prefers_foreground_duplicate_across_windows(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c002")

    def test_unlabeled_target_id_with_nearby_unlabeled_competitor_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click this button.",
            candidates=[
                ControlCandidate("c001", "", "button", (100, 10, 32, 32)),
                ControlCandidate("c002", "", "button", (140, 10, 32, 32)),
            ],
            model_rect=(140, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id ambiguous unlabeled control")

    def test_icon_only_target_id_with_nearby_icon_ignores_automation_ids_for_ambiguity(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click this icon.",
            candidates=[
                ControlCandidate("c001", "", "button", (100, 10, 32, 32), automation_id="helperPrecisionIconA"),
                ControlCandidate("c002", "", "button", (140, 10, 32, 32), automation_id="helperPrecisionIconB"),
            ],
            model_rect=(140, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id ambiguous unlabeled control")

    def test_resolve_text_match_beats_nearby_wrong_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        candidates = [
            ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30)),
            ControlCandidate("c002", "Submit", "button", (100, 10, 60, 30)),
        ]

        result = resolve_candidate_target(
            target_id="",
            instruction="Click the Submit button.",
            candidates=candidates,
            model_rect=(10, 10, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c002")

    def test_resolve_low_confidence_returns_none(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        candidates = [ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30))]

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Continue.",
            candidates=candidates,
        )

        self.assertIsNone(result)

    def test_resolve_unknown_target_id_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c999",
            instruction="Click Continue.",
            candidates=[ControlCandidate("c001", "Continue", "button", (10, 10, 60, 30))],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "unknown target_id")

    def test_ambiguous_text_match_returns_rejected_resolution(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30)),
                ControlCandidate("c002", "Save", "button", (100, 10, 60, 30)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "ambiguous text match")

    def test_text_match_ignores_same_visual_duplicate(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), automation_id="save-a"),
                ControlCandidate("c002", "Save", "button", (10, 10, 60, 30), automation_id="save-b"),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.target_id, "c001")

    def test_norm_rect_clips_partially_offscreen_candidate(self) -> None:
        from control_inventory import collect_control_candidates, format_candidates_for_prompt

        button = _make_button("Edge", -20, 120, 60, 30)
        window = _make_window("App", -40, 0, 200, 600, [button])
        desktop = _FakeDesktop([window])
        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        prompt = format_candidates_for_prompt(candidates, self._capture())

        self.assertIn("Edge", prompt)
        self.assertIn("norm=(0,200,50,50)", prompt)

    def test_collect_prefers_tighter_child_over_matching_container(self) -> None:
        from control_inventory import collect_control_candidates

        child = _make_button("Save", 120, 120, 50, 24)
        parent = _FakeControl(
            text="Save",
            control_type="Button",
            rect=_FakeRect(100, 100, 240, 180),
            children=[child],
        )
        window = _make_window("App", 0, 0, 800, 600, [parent])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].rect, (120, 120, 50, 24))

    def test_labeled_parent_is_not_pruned_by_unlabeled_child_glyph(self) -> None:
        from control_inventory import collect_control_candidates

        glyph = _FakeControl(
            text="",
            control_type="Button",
            rect=_FakeRect(104, 104, 124, 124),
        )
        parent = _FakeControl(
            text="Enable sync",
            control_type="CheckBox",
            rect=_FakeRect(100, 100, 220, 132),
            children=[glyph],
        )
        window = _make_window("Settings", 0, 0, 800, 600, [parent])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertTrue(any(candidate.text == "Enable sync" for candidate in candidates))

    def test_visible_parent_is_not_pruned_by_automation_only_child_glyph(self) -> None:
        from control_inventory import collect_control_candidates

        glyph = _FakeControl(
            text="",
            control_type="Button",
            rect=_FakeRect(104, 104, 124, 124),
            automation_id="saveChangesIcon",
        )
        parent = _FakeControl(
            text="Save changes",
            control_type="Button",
            rect=_FakeRect(100, 100, 240, 132),
            children=[glyph],
        )
        window = _make_window("Editor", 0, 0, 800, 600, [parent])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual(candidates[0].text, "Save changes")
        self.assertEqual(candidates[0].rect, (100, 100, 140, 32))
        self.assertTrue(any(candidate.automation_id == "saveChangesIcon" for candidate in candidates))

    def test_collect_prioritizes_foreground_window_before_screen_position(self) -> None:
        from control_inventory import collect_control_candidates

        background_buttons = [
            _make_button(f"Browser {index}", 10 + index * 12, 10, 10, 24)
            for index in range(12)
        ]
        background = _make_window("Browser", 0, 0, 800, 120, background_buttons, handle=101)
        save = _make_button("Save changes", 120, 500, 90, 32)
        foreground = _make_window("Editor", 0, 420, 800, 180, [save], handle=202)
        desktop = _FakeDesktop([background, foreground])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            foreground_handle_provider=lambda: 202,
            timeout_ms=2000,
            limit=3,
        )

        self.assertTrue(candidates)
        self.assertEqual(candidates[0].text, "Save changes")
        self.assertEqual(candidates[0].id, "c001")

    def test_collect_visits_foreground_window_before_background_windows(self) -> None:
        from control_inventory import collect_control_candidates

        visits: list[str] = []
        background = _RecordingControl(
            text="Background",
            control_type="Window",
            rect=_FakeRect(0, 0, 800, 300),
            handle=101,
            children=[_make_button("Background button", 20, 20, 120, 30)],
            visits=visits,
        )
        foreground = _RecordingControl(
            text="Foreground",
            control_type="Window",
            rect=_FakeRect(0, 320, 800, 620),
            handle=202,
            children=[_make_button("Foreground button", 20, 340, 120, 30)],
            visits=visits,
        )
        desktop = _FakeDesktop([background, foreground])

        collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            foreground_handle_provider=lambda: 202,
            timeout_ms=2000,
        )

        self.assertGreaterEqual(len(visits), 2)
        self.assertEqual(visits[:2], ["Foreground", "Background"])

    def test_collect_skips_own_process_top_level_windows(self) -> None:
        from control_inventory import collect_control_candidates

        helper_button = _make_button("Helper settings", 20, 20, 120, 30)
        helper_window = _make_window("Helper", 0, 0, 200, 120, [helper_button], handle=101)
        app_button = _make_button("Save changes", 220, 20, 120, 30)
        app_window = _make_window("Editor", 200, 0, 300, 120, [app_button], handle=202)
        desktop = _FakeDesktop([helper_window, app_window])

        with patch("control_inventory._is_own_process_window", side_effect=lambda hwnd: hwnd == 101):
            candidates = collect_control_candidates(
                self._capture(),
                desktop_factory=lambda: desktop,
                timeout_ms=2000,
            )

        self.assertEqual([candidate.text for candidate in candidates], ["Save changes"])
        self.assertEqual(candidates[0].window_title, "Editor")

    def test_collect_skips_occluded_background_window_candidates(self) -> None:
        from control_inventory import collect_control_candidates

        save = _make_button("Save changes", 40, 40, 120, 30)
        background = _make_window("Background Editor", 0, 0, 240, 140, [save], handle=101)
        dismiss = _make_button("Dismiss", 50, 45, 100, 30)
        foreground = _make_window("Blocking Dialog", 20, 20, 220, 120, [dismiss], handle=202)
        desktop = _FakeDesktop([background, foreground])

        def topmost_at(x: int, y: int) -> int:
            if 20 <= x < 240 and 20 <= y < 140:
                return 202
            return 101

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            topmost_handle_provider=topmost_at,
            timeout_ms=2000,
        )

        labels = [candidate.text for candidate in candidates]
        self.assertIn("Dismiss", labels)
        self.assertNotIn("Save changes", labels)

    def test_collect_skips_candidate_when_click_center_is_occluded(self) -> None:
        from control_inventory import collect_control_candidates

        save = _make_button("Save changes", 40, 40, 120, 30)
        background = _make_window("Background Editor", 0, 0, 240, 140, [save], handle=101)
        dismiss = _make_button("Dismiss", 90, 45, 80, 30)
        foreground = _make_window("Blocking Dialog", 80, 35, 100, 50, [dismiss], handle=202)
        desktop = _FakeDesktop([background, foreground])

        def topmost_at(x: int, y: int) -> int:
            if 80 <= x < 180 and 35 <= y < 85:
                return 202
            return 101

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            topmost_handle_provider=topmost_at,
            timeout_ms=2000,
        )

        labels = [candidate.text for candidate in candidates]
        self.assertIn("Dismiss", labels)
        self.assertNotIn("Save changes", labels)

    def test_snap_candidate_target_reuses_collected_candidate_snapshot(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[ControlCandidate("c001", "Save", "button", (100, 100, 50, 24))],
            model_rect=(96, 96, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.rect, (100, 100, 50, 24))

    def test_snap_candidate_target_accepts_deictic_exact_labeled_control(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click here.",
            candidates=[ControlCandidate("c001", "Save", "button", (100, 100, 80, 32))],
            model_rect=(100, 100, 80, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (100, 100, 80, 32))

    def test_snap_candidate_target_prefers_tight_action_inside_matching_row(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Settings.",
            candidates=[
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Settings", "button", (20, 20, 70, 30)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")
        self.assertEqual(result.rect, (20, 20, 70, 30))

    def test_snap_candidate_target_rejects_generic_row_containing_tight_actions(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this button.",
            candidates=[
                ControlCandidate("c001", "Account row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Edit", "button", (450, 20, 60, 30)),
                ControlCandidate("c003", "Delete", "button", (520, 20, 70, 30)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_prefers_splitbutton_menu_segment(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Open the Export menu.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
            model_rect=(100, 100, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c003")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (240, 100, 40, 32))

    def test_snap_candidate_target_prefers_splitbutton_dropdown_segment(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Open the Export dropdown.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
            model_rect=(100, 100, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c003")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (240, 100, 40, 32))

    def test_snap_candidate_target_rejects_splitbutton_menu_without_precise_segment(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Open the Export menu.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
            ],
            model_rect=(100, 100, 180, 32),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_menu_launcher_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            "Open the overflow menu.",
            "Click the kebab menu.",
            "Click the three dots menu.",
            "Open the More options menu.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "More options", "button", (10, 10, 120, 32)),
                    ],
                    model_rect=(10, 10, 120, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_snap_candidate_target_accepts_common_button_aliases(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Click Confirm.", "OK", "ok"),
            ("Click Previous.", "Back", "back"),
            ("Click Continue.", "Next", "next"),
            ("Click Sign in.", "Log in", "login"),
        )
        for instruction, label, candidate_id in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate(candidate_id, label, "button", (10, 10, 120, 32)),
                    ],
                    model_rect=(10, 10, 120, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, candidate_id)
                self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_prefers_disclosure_button_inside_broad_row(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Click the chevron.", "Expand", "c002"),
            ("Click the down arrow.", "Expand", "c002"),
            ("Expand Advanced settings.", "Expand", "c002"),
            ("Collapse Advanced settings.", "Collapse", "c003"),
        )
        for instruction, label, candidate_id in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "Advanced settings", "listitem", (10, 10, 500, 80)),
                        ControlCandidate(candidate_id, label, "button", (468, 36, 28, 28)),
                    ],
                    model_rect=(10, 10, 500, 80),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, candidate_id)
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, (468, 36, 28, 28))

    def test_snap_candidate_target_rejects_selector_wording_on_plain_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Open the Country selector.",
            candidates=[
                ControlCandidate("c001", "Country", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_selector_combobox_in_broad_rect(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Open the Country selector.",
            candidates=[
                ControlCandidate("c001", "Country", "combobox", (10, 10, 220, 32)),
                ControlCandidate("c002", "Country", "button", (280, 10, 100, 32)),
            ],
            model_rect=(10, 10, 370, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 220, 32))

    def test_snap_candidate_target_keeps_explicit_picker_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click the picker button.",
            candidates=[
                ControlCandidate("c001", "Picker", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_accepts_contextual_picker_launcher_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Open the Date picker.", "Date"),
            ("Click the Calendar picker.", "Calendar"),
            ("Open the Color picker.", "Color"),
            ("Click the File picker.", "Choose file"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (10, 10, 140, 32)),
                    ],
                    model_rect=(10, 10, 140, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_accepts_file_action_alias_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Upload a file.", "Browse"),
            ("Open the file picker.", "Browse"),
            ("Choose a file.", "Browse"),
            ("Attach a document.", "Choose file"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (10, 10, 140, 32)),
                    ],
                    model_rect=(10, 10, 140, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_accepts_generic_field_containing_clear_action(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this field.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 600, 40)),
                ControlCandidate("c002", "Clear", "button", (570, 14, 28, 28)),
            ],
            model_rect=(10, 10, 600, 40),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 600, 40))

    def test_snap_candidate_target_accepts_generic_text_box_without_placeholder_match(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this text box.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 600, 40)),
                ControlCandidate("c002", "Clear", "button", (570, 14, 28, 28)),
            ],
            model_rect=(10, 10, 600, 40),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 600, 40))

    def test_snap_candidate_target_accepts_generic_checkbox_label_without_type_text(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this checkbox.",
            candidates=[
                ControlCandidate("c001", "Enable precision mode", "checkbox", (10, 10, 200, 32)),
            ],
            model_rect=(10, 10, 200, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 200, 32))

    def test_snap_candidate_target_accepts_generic_toggle_checkbox(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this toggle.",
            candidates=[
                ControlCandidate("c001", "Dark mode", "checkbox", (10, 10, 200, 32)),
            ],
            model_rect=(10, 10, 200, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 200, 32))

    def test_snap_candidate_target_accepts_generic_switch_checkbox(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this switch.",
            candidates=[
                ControlCandidate("c001", "Dark mode", "checkbox", (10, 10, 200, 32)),
            ],
            model_rect=(10, 10, 200, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 200, 32))

    def test_snap_candidate_target_accepts_generic_radio_option(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Select this option.",
            candidates=[
                ControlCandidate("c001", "Weekly", "radiobutton", (10, 10, 140, 32)),
            ],
            model_rect=(10, 10, 140, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 140, 32))

    def test_snap_candidate_target_rejects_broad_radio_option_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Select this option.",
            candidates=[
                ControlCandidate("c001", "Daily", "radiobutton", (10, 10, 140, 32)),
                ControlCandidate("c002", "Weekly", "radiobutton", (10, 42, 140, 32)),
                ControlCandidate("c003", "Monthly", "radiobutton", (10, 74, 140, 32)),
            ],
            model_rect=(10, 10, 140, 96),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_generic_slider(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Adjust this slider.",
            candidates=[
                ControlCandidate("c001", "Volume", "slider", (10, 10, 220, 32)),
            ],
            model_rect=(10, 10, 220, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 220, 32))

    def test_snap_candidate_target_rejects_broad_slider_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Adjust this slider.",
            candidates=[
                ControlCandidate("c001", "Volume", "slider", (10, 10, 220, 32)),
                ControlCandidate("c002", "Brightness", "slider", (10, 50, 220, 32)),
                ControlCandidate("c003", "Contrast", "slider", (10, 90, 220, 32)),
            ],
            model_rect=(10, 10, 220, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_generic_spinner(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Adjust this spinner.",
            candidates=[
                ControlCandidate("c001", "History max tokens", "spinner", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_snap_candidate_target_rejects_broad_spinner_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Adjust this spinner.",
            candidates=[
                ControlCandidate("c001", "Temperature", "spinner", (10, 10, 120, 32)),
                ControlCandidate("c002", "Retries", "spinner", (10, 50, 120, 32)),
                ControlCandidate("c003", "Delay", "spinner", (10, 90, 120, 32)),
            ],
            model_rect=(10, 10, 120, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_generic_hyperlink(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this hyperlink.",
            candidates=[
                ControlCandidate("c001", "Documentation", "hyperlink", (10, 10, 140, 28)),
            ],
            model_rect=(10, 10, 140, 28),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 140, 28))

    def test_snap_candidate_target_rejects_broad_hyperlink_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this hyperlink.",
            candidates=[
                ControlCandidate("c001", "Docs", "hyperlink", (10, 10, 120, 28)),
                ControlCandidate("c002", "Support", "hyperlink", (10, 46, 120, 28)),
                ControlCandidate("c003", "Pricing", "hyperlink", (10, 82, 120, 28)),
            ],
            model_rect=(10, 10, 120, 100),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_generic_list_item(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this list item.",
            candidates=[
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_snap_candidate_target_rejects_broad_list_item_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this list item.",
            candidates=[
                ControlCandidate("c001", "General", "listitem", (10, 10, 160, 32)),
                ControlCandidate("c002", "Privacy", "listitem", (10, 50, 160, 32)),
                ControlCandidate("c003", "Billing", "listitem", (10, 90, 160, 32)),
            ],
            model_rect=(10, 10, 160, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_generic_tree_item(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this tree item.",
            candidates=[
                ControlCandidate("c001", "Settings", "treeitem", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_snap_candidate_target_rejects_broad_tree_item_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this tree item.",
            candidates=[
                ControlCandidate("c001", "src", "treeitem", (10, 10, 160, 32)),
                ControlCandidate("c002", "tests", "treeitem", (10, 50, 160, 32)),
                ControlCandidate("c003", "docs", "treeitem", (10, 90, 160, 32)),
            ],
            model_rect=(10, 10, 160, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_compact_control_type_words(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("listitem", "listitem", (10, 10, 160, 32)),
            ("treeitem", "treeitem", (10, 10, 160, 32)),
            ("menuitem", "menuitem", (10, 10, 160, 28)),
            ("tabitem", "tabitem", (10, 10, 140, 32)),
            ("headeritem", "headeritem", (10, 10, 140, 28)),
            ("splitbutton", "splitbutton", (10, 10, 160, 32)),
        )
        for word, control_type, rect in cases:
            with self.subTest(word=word):
                result = snap_candidate_target(
                    instruction=f"Click this {word}.",
                    candidates=[
                        ControlCandidate("c001", "Settings", control_type, rect),
                    ],
                    model_rect=rect,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, rect)

    def test_snap_candidate_target_accepts_generic_split_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this split button.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_snap_candidate_target_rejects_broad_split_button_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this split button.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (10, 10, 160, 32)),
                ControlCandidate("c002", "Share", "splitbutton", (10, 50, 160, 32)),
                ControlCandidate("c003", "Archive", "splitbutton", (10, 90, 160, 32)),
            ],
            model_rect=(10, 10, 160, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_browser_address_bar_wording(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            "Focus the URL bar.",
            "Click the location bar.",
            "Click the omnibox.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "Address", "edit", (10, 10, 240, 32)),
                    ],
                    model_rect=(10, 10, 240, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, (10, 10, 240, 32))

    def test_snap_candidate_target_prefers_address_edit_in_broad_bar_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Focus the URL bar.",
            candidates=[
                ControlCandidate("c001", "Address", "edit", (10, 10, 240, 32)),
                ControlCandidate("c002", "Search", "edit", (10, 50, 240, 32)),
                ControlCandidate("c003", "Filter", "edit", (10, 90, 240, 32)),
            ],
            model_rect=(10, 10, 240, 112),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 240, 32))

    def test_snap_candidate_target_rejects_text_entry_wording_on_plain_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Type your email.", "Email", (10, 10, 180, 32)),
            ("Enter the verification code.", "Verification code", (10, 10, 220, 32)),
            ("Click the search bar.", "Search", (10, 10, 180, 32)),
            ("Click the find bar.", "Find", (10, 10, 180, 32)),
            ("Click the filter bar.", "Filter", (10, 10, 180, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", rect),
                    ],
                    model_rect=rect,
                )

                self.assertIsNone(result)

    def test_snap_candidate_target_prefers_search_bar_edit_over_same_label_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click the search bar.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 260, 32)),
                ControlCandidate("c002", "Search", "button", (300, 10, 90, 32)),
            ],
            model_rect=(10, 10, 380, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertEqual(result.rect, (10, 10, 260, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_rejects_state_and_choice_wording_on_plain_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Check Remember me.", "Remember me"),
            ("Uncheck Remember me.", "Remember me"),
            ("Tick Remember me.", "Remember me"),
            ("Turn on dark mode.", "Dark mode"),
            ("Enable notifications.", "Notifications"),
            ("Pick Daily choice.", "Daily"),
            ("Choose Weekly option.", "Weekly"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (10, 10, 180, 32)),
                    ],
                    model_rect=(10, 10, 180, 32),
                )

                self.assertIsNone(result)

    def test_snap_candidate_target_keeps_check_for_updates_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Check for updates.",
            candidates=[
                ControlCandidate("c001", "Check for updates", "button", (10, 10, 180, 32)),
            ],
            model_rect=(10, 10, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_accepts_button_control_suffix(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this button control.",
            candidates=[
                ControlCandidate("c001", "Submit", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_snap_candidate_target_accepts_literal_edit(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this edit control.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 240, 32)),
            ],
            model_rect=(10, 10, 240, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 240, 32))

    def test_snap_candidate_target_rejects_broad_literal_edit_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this edit.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 240, 32)),
                ControlCandidate("c002", "Filter", "edit", (10, 50, 240, 32)),
                ControlCandidate("c003", "Name", "edit", (10, 90, 240, 32)),
            ],
            model_rect=(10, 10, 240, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_contextual_container_wording(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Click this toolbar button.", "Save", "button", (10, 10, 100, 32)),
            ("Click Toolbar button.", "Toolbar", "button", (10, 10, 100, 32)),
            ("Click this toolbar icon.", "Settings", "button", (10, 10, 32, 32)),
            ("Click this form field.", "Name", "edit", (10, 10, 240, 32)),
            ("Click this dialog button.", "OK", "button", (10, 10, 80, 32)),
            ("Click this modal button.", "OK", "button", (10, 10, 80, 32)),
            ("Click this panel button.", "Save", "button", (10, 10, 100, 32)),
            ("Click Panel button.", "Panel", "button", (10, 10, 100, 32)),
            ("Click this table row.", "Order 123", "listitem", (10, 10, 240, 32)),
            ("Click this grid row.", "Order 123", "listitem", (10, 10, 240, 32)),
            ("Click this page link.", "Docs", "hyperlink", (10, 10, 120, 28)),
            ("Click this card checkbox.", "Done", "checkbox", (10, 10, 160, 32)),
            ("Click this section toggle.", "Dark mode", "checkbox", (10, 10, 160, 32)),
            ("Click this drawer item.", "Settings", "listitem", (10, 10, 160, 32)),
            ("Click this pane button.", "Apply", "button", (10, 10, 100, 32)),
            ("Click this popup menu item.", "Open", "menuitem", (10, 10, 120, 28)),
            ("Click this navigation tab.", "Settings", "tabitem", (10, 10, 140, 32)),
            ("Click this sidebar item.", "Settings", "listitem", (10, 10, 160, 32)),
            ("Click this nav item.", "Settings", "listitem", (10, 10, 160, 32)),
        )
        for instruction, label, control_type, rect in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, control_type, rect),
                    ],
                    model_rect=rect,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, rect)

    def test_snap_candidate_target_rejects_broad_sidebar_item_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this sidebar item.",
            candidates=[
                ControlCandidate("c001", "General", "listitem", (10, 10, 160, 32)),
                ControlCandidate("c002", "Privacy", "listitem", (10, 50, 160, 32)),
                ControlCandidate("c003", "Billing", "listitem", (10, 90, 160, 32)),
            ],
            model_rect=(10, 10, 160, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_rejects_broad_table_row_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this table row.",
            candidates=[
                ControlCandidate("c001", "Order 1", "listitem", (10, 10, 240, 32)),
                ControlCandidate("c002", "Order 2", "listitem", (10, 50, 240, 32)),
                ControlCandidate("c003", "Order 3", "listitem", (10, 90, 240, 32)),
            ],
            model_rect=(10, 10, 240, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_allows_toggle_sidebar_button_label(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Toggle sidebar.",
            candidates=[
                ControlCandidate("c001", "Toggle sidebar", "button", (10, 10, 150, 32)),
                ControlCandidate("c002", "Dark mode", "checkbox", (10, 60, 140, 32)),
            ],
            model_rect=(10, 10, 150, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 150, 32))

    def test_snap_candidate_target_accepts_generic_column_header(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this column header.",
            candidates=[
                ControlCandidate("c001", "Status", "headeritem", (100, 50, 120, 28)),
            ],
            model_rect=(100, 50, 120, 28),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (100, 50, 120, 28))

    def test_snap_candidate_target_rejects_broad_header_row_without_label(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this column header.",
            candidates=[
                ControlCandidate("c001", "Name", "headeritem", (20, 50, 120, 28)),
                ControlCandidate("c002", "Status", "headeritem", (140, 50, 120, 28)),
                ControlCandidate("c003", "Owner", "headeritem", (260, 50, 120, 28)),
            ],
            model_rect=(20, 50, 360, 28),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_rejects_checkbox_intent_on_unlabeled_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this checkbox.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_uses_single_checkbox_inside_loose_row(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this checkbox.",
            candidates=[
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 34, 20, 20)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (24, 34, 20, 20))

    def test_snap_candidate_target_uses_single_checkbox_inside_contextual_row(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click the checkbox in Task row.",
            candidates=[
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 34, 20, 20)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (24, 34, 20, 20))

    def test_snap_candidate_target_rejects_multiple_contextual_checkboxes(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click the checkbox in Task row.",
            candidates=[
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 24, 20, 20)),
                ControlCandidate("c003", "Archived", "checkbox", (24, 52, 20, 20)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_rejects_multiple_checkboxes_inside_loose_row(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this checkbox.",
            candidates=[
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 24, 20, 20)),
                ControlCandidate("c003", "Archived", "checkbox", (24, 52, 20, 20)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_ignores_same_visual_duplicate(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (100, 100, 50, 24), automation_id="save-a"),
                ControlCandidate("c002", "Save", "button", (100, 100, 50, 24), automation_id="save-b"),
            ],
            model_rect=(96, 96, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")

    def test_snap_candidate_target_prefers_foreground_duplicate_when_geometry_is_close(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (80, 100, 80, 32), window_rank=2),
                ControlCandidate("c002", "Save", "button", (170, 100, 80, 32), window_rank=0),
            ],
            model_rect=(130, 100, 80, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")

    def test_snap_candidate_target_rejects_exact_background_duplicate(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this button.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (120, 100, 80, 32), window_rank=0),
                ControlCandidate("c002", "Save", "button", (120, 145, 80, 32), window_rank=2),
            ],
            model_rect=(120, 145, 80, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")
        self.assertEqual(result.rejected_reason, "ambiguous candidate snap")

    def test_snap_candidate_target_rejects_automation_only_when_visible_alternative_exists(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (160, 10, 80, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_rejects_visible_text_conflict_despite_matching_automation_id(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Cancel",
                    "button",
                    (100, 100, 50, 24),
                    automation_id="saveButton",
                )
            ],
            model_rect=(100, 100, 50, 24),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")


class HelpTargetHarnessTests(unittest.TestCase):
    def _capture(self):
        from screen import Capture

        return Capture(
            png_bytes=b"png",
            width=1000,
            height=1000,
            monitor_left=0,
            monitor_top=0,
            scale=1.0,
        )

    def _decision(self, payload: dict):
        from agent import _parse_live_help_decision
        import json

        return _parse_live_help_decision(json.dumps(payload))

    def test_target_id_uses_candidate_rect_not_model_rect(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 100, "y": 150, "width": 120, "height": 60},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Save", "button", (120, 160, 80, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rect, (120, 160, 80, 32))

    def test_wrong_target_id_recovers_by_text_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c002",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Save", "button", (120, 160, 80, 32)),
                ControlCandidate("c002", "Cancel", "button", (260, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")

    def test_text_entry_action_wrong_target_id_recovers_to_edit(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Type your email.",
                    "target_id": "c002",
                    "target": {"x": 300, "y": 160, "width": 90, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Email", "edit", (120, 160, 160, 32)),
                ControlCandidate("c002", "Email", "button", (300, 160, 90, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 160, 32))
        self.assertFalse(target.rejected_reason)

    def test_search_bar_model_rect_rejects_plain_button_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the search bar.",
                    "target": {"x": 300, "y": 160, "width": 90, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Search", "button", (300, 160, 90, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_state_action_wrong_target_id_recovers_to_checkbox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Check Remember me.",
                    "target_id": "c002",
                    "target": {"x": 300, "y": 160, "width": 90, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Remember me", "checkbox", (120, 160, 160, 32)),
                ControlCandidate("c002", "Remember me", "button", (300, 160, 90, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 160, 32))
        self.assertFalse(target.rejected_reason)

    def test_choice_wording_wrong_target_id_recovers_to_radio(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Pick Daily choice.",
                    "target_id": "c002",
                    "target": {"x": 300, "y": 160, "width": 90, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Daily", "radiobutton", (120, 160, 160, 32)),
                ControlCandidate("c002", "Daily", "button", (300, 160, 90, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 160, 32))
        self.assertFalse(target.rejected_reason)

    def test_check_for_updates_model_rect_keeps_button_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Check for updates.",
                    "target": {"x": 120, "y": 160, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Check for updates", "button", (120, 160, 180, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)

    def test_save_action_target_id_accepts_floppy_disk_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Save document.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Floppy disk", "button", (120, 160, 120, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 120, 32))
        self.assertFalse(target.rejected_reason)

    def test_floppy_disk_action_target_id_accepts_save_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the floppy disk.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Save", "button", (120, 160, 100, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 100, 32))
        self.assertFalse(target.rejected_reason)

    def test_save_symbol_target_id_accepts_icon(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Save document.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "\U0001f4be", "button", (120, 160, 32, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 32, 32))
        self.assertFalse(target.rejected_reason)

    def test_save_text_match_overrides_cancel_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Save document.",
                    "target": {"x": 300, "y": 160, "width": 140, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Floppy disk", "button", (120, 160, 120, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 120, 32))
        self.assertFalse(target.rejected_reason)

    def test_unlabeled_target_id_with_geometry_recovers_to_visible_text_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 10, "y": 10, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "", "button", (10, 10, 32, 32)),
                ControlCandidate("c002", "Save", "button", (100, 10, 60, 30)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (100, 10, 60, 30))
        self.assertFalse(target.rejected_reason)

    def test_model_rect_on_automation_only_candidate_recovers_to_visible_text_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 10, "y": 10, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (120, 10, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (120, 10, 80, 32))
        self.assertFalse(target.rejected_reason)

    def test_automation_only_target_id_recovers_to_visible_text_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 10, "y": 10, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (120, 10, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (120, 10, 80, 32))
        self.assertFalse(target.rejected_reason)

    def test_background_target_id_with_geometry_does_not_resnap_same_rejected_target(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 10, "y": 10, "width": 60, "height": 30},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_wrong_target_id_recovers_by_geometry_when_text_is_ambiguous(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 120, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Cancel", "button", (20, 160, 80, 32)),
                ControlCandidate("c002", "Save", "button", (120, 160, 80, 32)),
                ControlCandidate("c003", "Save", "button", (260, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 80, 32))

    def test_unknown_target_id_without_rect_downgrades_no_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c999",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Save", "button", (120, 160, 80, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rejected_reason, "unknown target_id")

    def test_unknown_target_id_with_rect_does_not_fall_back_to_raw_model_rect(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target
        from rect_snap import SnapResult

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c999",
                    "target": {"x": 400, "y": 400, "width": 70, "height": 30},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Cancel", "button", (120, 160, 80, 32))],
            snapper=lambda rect, _instruction: SnapResult(rect=rect, confidence=0.0, source="model"),
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rejected_reason, "unknown target_id")

    def test_model_rect_snaps_to_candidate_snapshot_without_fresh_uia(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        calls: list[bool] = []

        def snapper(_rect, _instruction):
            calls.append(True)
            raise AssertionError("fresh UIA snapper should not be called")

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this button.",
                    "target": {"x": 105, "y": 155, "width": 105, "height": 50},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Save", "button", (120, 160, 80, 32))],
            snapper=snapper,
        )

        self.assertFalse(calls)
        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rect, (120, 160, 80, 32))

    def test_loose_row_model_rect_snaps_to_tight_child_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Settings.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Settings", "button", (20, 20, 70, 30)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (20, 20, 70, 30))
        self.assertFalse(target.rejected_reason)

    def test_row_target_id_recovers_to_tight_child_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Settings.",
                    "target_id": "c001",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Settings", "button", (20, 20, 70, 30)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (20, 20, 70, 30))
        self.assertFalse(target.rejected_reason)

    def test_splitbutton_target_id_recovers_to_menu_segment(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the Export menu.",
                    "target_id": "c001",
                    "target": {"x": 100, "y": 100, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c003")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (240, 100, 40, 32))

    def test_splitbutton_model_rect_highlights_menu_segment(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the Export menu.",
                    "target": {"x": 100, "y": 100, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c003")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (240, 100, 40, 32))

    def test_menu_launcher_target_id_highlights_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the overflow menu.",
                    "target_id": "c001",
                    "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "More options", "button", (120, 160, 120, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_menu_launcher_model_rect_highlights_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the three dots menu.",
                    "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "More options", "button", (120, 160, 120, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_contextual_menu_wording_highlights_launcher_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open the profile menu.", "Profile"),
            ("Open the account menu.", "Account"),
            ("Open the user menu.", "User"),
            ("Open the settings menu.", "Settings"),
            ("Open the account dropdown.", "Account"),
            ("Open the profile drop down.", "Profile"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", label, "button", (120, 160, 120, 32)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_contextual_menu_item_wording_still_highlights_menuitem(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the profile menu item.",
                    "target": {"x": 120, "y": 160, "width": 240, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Profile", "button", (120, 160, 120, 32)),
                ControlCandidate("c002", "Profile", "menuitem", (120, 210, 240, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 210, 240, 32))

    def test_common_alias_model_rect_highlights_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Confirm.",
                    "target": {"x": 120, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "OK", "button", (120, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 80, 32))

    def test_symbol_only_question_mark_target_id_accepts_without_model_rect(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the question mark.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "?", "button", (120, 160, 32, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_symbol_only_text_match_overrides_wrong_model_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the question mark.",
                    "target": {"x": 220, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "?", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Help", "button", (220, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_symbol_only_action_buttons_match_instruction_text(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Add a new item.", "+", (120, 160, 32, 32)),
            ("Open the more options menu.", "...", (120, 160, 32, 32)),
            ("Close the dialog.", "\u00d7", (120, 160, 32, 32)),
            ("Open settings.", "\u2699", (120, 160, 32, 32)),
            ("Search.", "\U0001f50d", (120, 160, 32, 32)),
        )
        for instruction, text, rect in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", text, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_copy_action_alias_target_id_accepts_duplicate_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Copy this item.", "Duplicate"),
            ("Clone this item.", "Duplicate"),
            ("Duplicate this item.", "Copy"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 100, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_copy_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Copy this item.",
                    "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Duplicate", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_copy_action_alias_rejects_ambiguous_copy_and_duplicate_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Copy this item.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Duplicate", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Copy", "button", (260, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_transfer_and_refresh_alias_target_id_accepts_matching_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Download the report.", "Export"),
            ("Export the report.", "Download"),
            ("Import data.", "Upload"),
            ("Upload data.", "Import"),
            ("Refresh the page.", "Reload"),
            ("Reload the page.", "Refresh"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 120, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_download_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Download the report.",
                    "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "button", (120, 160, 120, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_transfer_alias_rejects_ambiguous_download_and_export_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Download the report.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "button", (120, 160, 120, 32)),
                ControlCandidate("c002", "Download", "button", (280, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_send_action_alias_target_id_accepts_submit_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Send the message.", "Submit"),
            ("Submit the form.", "Send"),
            ("Send the message.", "Paper plane"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 100, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_send_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Send the message.", "Submit"),
            ("Submit the form.", "Send"),
            ("Send message.", "Paper plane"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", label, "button", (120, 160, 100, 32)),
                        ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_send_action_alias_text_match_overrides_message_field_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Send message.",
                    "target": {"x": 300, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Paper plane", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Message", "edit", (300, 160, 220, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_send_alias_prefers_exact_send_over_submit_alias(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Send the message.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Submit", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Send", "button", (280, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (280, 160, 100, 32))

    def test_meeting_control_alias_target_id_accepts_common_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Mute microphone.", "Mic", (120, 160, 80, 32)),
            ("Mute mic.", "Microphone", (120, 160, 120, 32)),
            ("Mute audio.", "Speaker", (120, 160, 100, 32)),
            ("Mute speaker.", "Sound", (120, 160, 90, 32)),
            ("Open volume.", "Speaker", (120, 160, 100, 32)),
            ("Start video.", "Camera", (120, 160, 100, 32)),
            ("Start camera.", "Video", (120, 160, 90, 32)),
            ("Start webcam.", "Camera", (120, 160, 100, 32)),
            ("Mute microphone.", "\U0001f3a4", (120, 160, 32, 32)),
            ("Mute audio.", "\U0001f50a", (120, 160, 32, 32)),
            ("Mute audio.", "\U0001f507", (120, 160, 32, 32)),
            ("Start video.", "\U0001f4f7", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_meeting_control_alias_text_match_overrides_settings_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Mute microphone.",
                ControlCandidate("c001", "Mic", "button", (120, 160, 80, 32)),
                ControlCandidate("c002", "Audio settings", "button", (300, 160, 140, 32)),
            ),
            (
                "Mute audio.",
                ControlCandidate("c001", "Speaker", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Audio settings", "button", (300, 160, 140, 32)),
            ),
            (
                "Start video.",
                ControlCandidate("c001", "Camera", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "AV settings", "button", (300, 160, 150, 32)),
            ),
        )
        for instruction, expected, decoy in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": decoy.rect[0],
                                "y": decoy.rect[1],
                                "width": decoy.rect[2],
                                "height": decoy.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [expected, decoy],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected.rect)

    def test_meeting_control_alias_rejects_ambiguous_exact_and_alias_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Mute microphone.",
                ControlCandidate("c001", "Mic", "button", (120, 160, 80, 32)),
                ControlCandidate("c002", "Microphone", "button", (240, 160, 120, 32)),
            ),
            (
                "Mute speaker.",
                ControlCandidate("c001", "Speaker", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Sound", "button", (240, 160, 90, 32)),
            ),
            (
                "Start video.",
                ControlCandidate("c001", "Camera", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Video", "button", (240, 160, 90, 32)),
            ),
        )
        for instruction, target_candidate, competing_candidate in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [target_candidate, competing_candidate],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_audio_settings_instruction_targets_settings_not_speaker_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open audio settings.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Audio settings", "button", (300, 160, 140, 32)),
                ControlCandidate("c002", "Speaker", "button", (120, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (300, 160, 140, 32))

    def test_security_control_alias_target_id_accepts_common_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Lock screen.", "Padlock", (120, 160, 110, 32)),
            ("Click the padlock.", "Lock", (120, 160, 80, 32)),
            ("Unlock account.", "Lock", (120, 160, 80, 32)),
            ("Open security.", "Shield", (120, 160, 90, 32)),
            ("Click shield.", "Security", (120, 160, 110, 32)),
            ("Lock screen.", "\U0001f512", (120, 160, 32, 32)),
            ("Open security.", "\U0001f6e1", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_lock_alias_text_match_overrides_security_settings_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Lock screen.",
                    "target": {"x": 300, "y": 160, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Padlock", "button", (120, 160, 110, 32)),
                ControlCandidate("c002", "Security settings", "button", (300, 160, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 110, 32))

    def test_security_settings_instruction_targets_settings_not_shield_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open security settings.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Security settings", "button", (300, 160, 160, 32)),
                ControlCandidate("c002", "Shield", "button", (120, 160, 90, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (300, 160, 160, 32))

    def test_cart_action_alias_target_id_accepts_common_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open cart.", "Basket", (120, 160, 100, 32)),
            ("Open basket.", "Cart", (120, 160, 100, 32)),
            ("Open cart.", "Shopping bag", (120, 160, 130, 32)),
            ("Open cart.", "\U0001f6d2", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_cart_action_alias_text_match_overrides_shopping_options_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open cart.",
                    "target": {"x": 300, "y": 160, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Basket", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Shopping options", "button", (300, 160, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_cart_alias_rejects_ambiguous_cart_and_basket_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open cart.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Basket", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Cart", "button", (280, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_password_visibility_target_id_accepts_eye_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Show password.", "Eye", (360, 160, 32, 32)),
            ("Hide password.", "Visibility", (360, 160, 80, 32)),
            ("Reveal passcode.", "Eye", (360, 160, 32, 32)),
            ("Show password.", "\U0001f441", (360, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", label, "button", rect),
                        ControlCandidate("c002", "Password", "edit", (120, 160, 220, 32)),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_password_visibility_text_match_overrides_password_field_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Show password.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Eye", "button", (360, 160, 32, 32)),
                ControlCandidate("c002", "Password", "edit", (120, 160, 220, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (360, 160, 32, 32))

    def test_show_sidebar_does_not_match_eye_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Show sidebar.",
                    "target": {"x": 120, "y": 160, "width": 150, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Eye", "button", (360, 160, 32, 32)),
                ControlCandidate("c002", "Show sidebar", "button", (120, 160, 150, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 150, 32))

    def test_navigation_and_time_alias_target_id_accepts_common_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open calendar.", "Date", (120, 160, 100, 32)),
            ("Open date picker.", "Calendar", (120, 160, 120, 32)),
            ("Open clock.", "Time", (120, 160, 100, 32)),
            ("Open time picker.", "Clock", (120, 160, 100, 32)),
            ("Go home.", "House", (120, 160, 100, 32)),
            ("Click the house.", "Home", (120, 160, 100, 32)),
            ("Open calendar.", "\U0001f4c5", (120, 160, 32, 32)),
            ("Go home.", "\U0001f3e0", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_calendar_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open calendar.",
                    "target": {"x": 300, "y": 160, "width": 140, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Date", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_home_alias_rejects_ambiguous_home_and_house_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Go home.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "House", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Home", "button", (280, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_print_action_alias_target_id_accepts_printer_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Print document.", "Printer", (120, 160, 100, 32)),
            ("Open printer.", "Print", (120, 160, 100, 32)),
            ("Print document.", "\U0001f5a8", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_print_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Print document.",
                    "target": {"x": 300, "y": 160, "width": 140, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Printer", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_print_alias_rejects_ambiguous_print_and_printer_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Print document.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Printer", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Print", "button", (280, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_folder_action_alias_target_id_accepts_directory_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open folder.", "Directory", (120, 160, 120, 32)),
            ("Open directory.", "Folder", (120, 160, 100, 32)),
            ("Open folder.", "\U0001f4c1", (120, 160, 32, 32)),
            ("Open directory.", "\U0001f4c2", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_folder_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open folder.",
                    "target": {"x": 300, "y": 160, "width": 140, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Directory", "button", (120, 160, 120, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_folder_alias_rejects_ambiguous_folder_and_directory_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open folder.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Directory", "button", (120, 160, 120, 32)),
                ControlCandidate("c002", "Folder", "button", (280, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_favorite_action_alias_target_id_accepts_star_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Favorite this item.", "Star", (120, 160, 100, 32)),
            ("Star this item.", "Favorite", (120, 160, 120, 32)),
            ("Bookmark this item.", "Star", (120, 160, 100, 32)),
            ("Favorite this item.", "\u2606", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_favorite_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Favorite this item.",
                    "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Star", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_favorite_alias_rejects_ambiguous_favorite_and_star_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Favorite this item.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Star", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Favorite", "button", (280, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_notification_action_alias_target_id_accepts_bell_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Click the bell.", "Notifications", (120, 160, 140, 32)),
            ("Open notifications.", "Bell", (120, 160, 100, 32)),
            ("Open alerts.", "Bell", (120, 160, 100, 32)),
            ("Open notifications.", "\U0001f514", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_notification_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the bell.",
                    "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Notifications", "button", (120, 160, 140, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 140, 32))

    def test_notification_alias_rejects_ambiguous_bell_and_notifications_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open notifications.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Bell", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Notifications", "button", (280, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_disclosure_broad_row_highlights_single_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Click the chevron.", "Expand", "c002"),
            ("Expand Advanced settings.", "Expand", "c002"),
            ("Collapse Advanced settings.", "Collapse", "c003"),
        )
        for instruction, label, candidate_id in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 500, "height": 80},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", "Advanced settings", "listitem", (120, 160, 500, 80)),
                        ControlCandidate(candidate_id, label, "button", (578, 186, 28, 28)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, candidate_id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (578, 186, 28, 28))

    def test_selector_wrong_target_id_recovers_to_combobox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the Country selector.",
                    "target_id": "c002",
                    "target": {"x": 400, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Country", "combobox", (120, 160, 220, 32)),
                ControlCandidate("c002", "Country", "button", (400, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 220, 32))

    def test_selector_model_rect_highlights_combobox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the Country picker.",
                    "target": {"x": 400, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Country", "combobox", (120, 160, 220, 32)),
                ControlCandidate("c002", "Country", "button", (400, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 220, 32))

    def test_contextual_picker_model_rect_highlights_launcher_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the Date picker.",
                    "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Date", "button", (120, 160, 120, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_file_action_alias_model_rect_highlights_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Upload a file.",
                    "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Browse", "button", (120, 160, 120, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_splitbutton_model_rect_highlights_dropdown_segment(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the Export dropdown.",
                    "target": {"x": 100, "y": 100, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c003")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (240, 100, 40, 32))

    def test_generic_row_model_rect_with_actions_downgrades_no_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this button.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Account row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Edit", "button", (450, 20, 60, 30)),
                ControlCandidate("c003", "Delete", "button", (520, 20, 70, 30)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_field_model_rect_with_clear_action_highlights_field(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this field.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 40},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Search", "edit", (10, 10, 600, 40)),
                ControlCandidate("c002", "Clear", "button", (570, 14, 28, 28)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 600, 40))

    def test_generic_checkbox_model_rect_does_not_highlight_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this checkbox.",
                    "target": {"x": 10, "y": 10, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "", "button", (10, 10, 32, 32))],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_checkbox_row_model_rect_highlights_single_checkbox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this checkbox.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 34, 20, 20)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (24, 34, 20, 20))

    def test_generic_toggle_model_rect_highlights_checkbox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this toggle.",
                    "target": {"x": 10, "y": 10, "width": 200, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Dark mode", "checkbox", (10, 10, 200, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 200, 32))

    def test_generic_switch_model_rect_highlights_checkbox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this switch.",
                    "target": {"x": 10, "y": 10, "width": 200, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Dark mode", "checkbox", (10, 10, 200, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 200, 32))

    def test_toggle_sidebar_model_rect_highlights_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Toggle sidebar.",
                    "target": {"x": 10, "y": 10, "width": 150, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Toggle sidebar", "button", (10, 10, 150, 32)),
                ControlCandidate("c002", "Dark mode", "checkbox", (10, 60, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 150, 32))

    def test_generic_option_model_rect_highlights_radio(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Select this option.",
                    "target": {"x": 10, "y": 10, "width": 140, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Weekly", "radiobutton", (10, 10, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 140, 32))

    def test_generic_option_broad_group_rejects_multiple_radios(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Select this option.",
                    "target": {"x": 10, "y": 10, "width": 140, "height": 96},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Daily", "radiobutton", (10, 10, 140, 32)),
                ControlCandidate("c002", "Weekly", "radiobutton", (10, 42, 140, 32)),
                ControlCandidate("c003", "Monthly", "radiobutton", (10, 74, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_slider_model_rect_highlights_slider(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Adjust this slider.",
                    "target": {"x": 10, "y": 10, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Volume", "slider", (10, 10, 220, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 220, 32))

    def test_generic_spinner_model_rect_highlights_spinner(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Adjust this spinner.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "History max tokens", "spinner", (10, 10, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 160, 32))

    def test_generic_hyperlink_model_rect_highlights_hyperlink(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this hyperlink.",
                    "target": {"x": 10, "y": 10, "width": 140, "height": 28},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Documentation", "hyperlink", (10, 10, 140, 28)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 140, 28))

    def test_generic_list_item_model_rect_highlights_listitem(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this list item.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 160, 32))

    def test_generic_tree_item_model_rect_highlights_treeitem(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this tree item.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Settings", "treeitem", (10, 10, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 160, 32))

    def test_compact_control_type_model_rect_highlights_exact_type(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("listitem", "listitem", (10, 10, 160, 32)),
            ("treeitem", "treeitem", (10, 10, 160, 32)),
            ("menuitem", "menuitem", (10, 10, 160, 28)),
            ("tabitem", "tabitem", (10, 10, 140, 32)),
            ("headeritem", "headeritem", (10, 10, 140, 28)),
            ("splitbutton", "splitbutton", (10, 10, 160, 32)),
        )
        for word, control_type, rect in cases:
            with self.subTest(word=word):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": f"Click this {word}.",
                            "target": {
                                "x": rect[0],
                                "y": rect[1],
                                "width": rect[2],
                                "height": rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", "Settings", control_type, rect),
                    ],
                )

                self.assertEqual(target.source, "candidate_snap")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_generic_split_button_model_rect_highlights_splitbutton(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this split button.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "splitbutton", (10, 10, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 160, 32))

    def test_generic_split_button_broad_group_rejects_multiple_splitbuttons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this split button.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "splitbutton", (10, 10, 160, 32)),
                ControlCandidate("c002", "Share", "splitbutton", (10, 50, 160, 32)),
                ControlCandidate("c003", "Archive", "splitbutton", (10, 90, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_browser_url_bar_model_rect_highlights_address_edit(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Focus the URL bar.",
                    "target": {"x": 10, "y": 10, "width": 240, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Address", "edit", (10, 10, 240, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 240, 32))

    def test_browser_url_bar_broad_group_prefers_address_edit(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Focus the URL bar.",
                    "target": {"x": 10, "y": 10, "width": 240, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Address", "edit", (10, 10, 240, 32)),
                ControlCandidate("c002", "Search", "edit", (10, 50, 240, 32)),
                ControlCandidate("c003", "Filter", "edit", (10, 90, 240, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 240, 32))

    def test_button_control_suffix_model_rect_highlights_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this button control.",
                    "target": {"x": 10, "y": 10, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Submit", "button", (10, 10, 120, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 120, 32))

    def test_literal_edit_model_rect_highlights_edit(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this edit control.",
                    "target": {"x": 10, "y": 10, "width": 240, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Search", "edit", (10, 10, 240, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 240, 32))

    def test_contextual_container_model_rect_highlights_exact_control(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Click this toolbar button.", "Save", "button", (10, 10, 100, 32)),
            ("Click Toolbar button.", "Toolbar", "button", (10, 10, 100, 32)),
            ("Click this toolbar icon.", "Settings", "button", (10, 10, 32, 32)),
            ("Click this form field.", "Name", "edit", (10, 10, 240, 32)),
            ("Click this dialog button.", "OK", "button", (10, 10, 80, 32)),
            ("Click this modal button.", "OK", "button", (10, 10, 80, 32)),
            ("Click this panel button.", "Save", "button", (10, 10, 100, 32)),
            ("Click Panel button.", "Panel", "button", (10, 10, 100, 32)),
            ("Click this table row.", "Order 123", "listitem", (10, 10, 240, 32)),
            ("Click this grid row.", "Order 123", "listitem", (10, 10, 240, 32)),
            ("Click this page link.", "Docs", "hyperlink", (10, 10, 120, 28)),
            ("Click this card checkbox.", "Done", "checkbox", (10, 10, 160, 32)),
            ("Click this section toggle.", "Dark mode", "checkbox", (10, 10, 160, 32)),
            ("Click this drawer item.", "Settings", "listitem", (10, 10, 160, 32)),
            ("Click this pane button.", "Apply", "button", (10, 10, 100, 32)),
            ("Click this popup menu item.", "Open", "menuitem", (10, 10, 120, 28)),
            ("Click this navigation tab.", "Settings", "tabitem", (10, 10, 140, 32)),
            ("Click this sidebar item.", "Settings", "listitem", (10, 10, 160, 32)),
            ("Click this nav item.", "Settings", "listitem", (10, 10, 160, 32)),
        )
        for instruction, label, control_type, rect in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": rect[0],
                                "y": rect[1],
                                "width": rect[2],
                                "height": rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", label, control_type, rect),
                    ],
                )

                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_sidebar_item_broad_group_rejects_multiple_listitems(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this sidebar item.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "General", "listitem", (10, 10, 160, 32)),
                ControlCandidate("c002", "Privacy", "listitem", (10, 50, 160, 32)),
                ControlCandidate("c003", "Billing", "listitem", (10, 90, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_table_row_broad_group_rejects_multiple_listitems(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this table row.",
                    "target": {"x": 10, "y": 10, "width": 240, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Order 1", "listitem", (10, 10, 240, 32)),
                ControlCandidate("c002", "Order 2", "listitem", (10, 50, 240, 32)),
                ControlCandidate("c003", "Order 3", "listitem", (10, 90, 240, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_slider_broad_group_rejects_multiple_sliders(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Adjust this slider.",
                    "target": {"x": 10, "y": 10, "width": 220, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Volume", "slider", (10, 10, 220, 32)),
                ControlCandidate("c002", "Brightness", "slider", (10, 50, 220, 32)),
                ControlCandidate("c003", "Contrast", "slider", (10, 90, 220, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_spinner_broad_group_rejects_multiple_spinners(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Adjust this spinner.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Temperature", "spinner", (10, 10, 160, 32)),
                ControlCandidate("c002", "Retries", "spinner", (10, 50, 160, 32)),
                ControlCandidate("c003", "Delay", "spinner", (10, 90, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_hyperlink_broad_group_rejects_multiple_hyperlinks(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this hyperlink.",
                    "target": {"x": 10, "y": 10, "width": 120, "height": 100},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Docs", "hyperlink", (10, 10, 120, 28)),
                ControlCandidate("c002", "Support", "hyperlink", (10, 46, 120, 28)),
                ControlCandidate("c003", "Pricing", "hyperlink", (10, 82, 120, 28)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_list_item_broad_group_rejects_multiple_listitems(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this list item.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "General", "listitem", (10, 10, 160, 32)),
                ControlCandidate("c002", "Privacy", "listitem", (10, 50, 160, 32)),
                ControlCandidate("c003", "Billing", "listitem", (10, 90, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_tree_item_broad_group_rejects_multiple_treeitems(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this tree item.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "src", "treeitem", (10, 10, 160, 32)),
                ControlCandidate("c002", "tests", "treeitem", (10, 50, 160, 32)),
                ControlCandidate("c003", "docs", "treeitem", (10, 90, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_column_header_model_rect_highlights_header(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this column header.",
                    "target": {"x": 100, "y": 50, "width": 120, "height": 28},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Status", "headeritem", (100, 50, 120, 28)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (100, 50, 120, 28))

    def test_generic_column_header_broad_row_rejects_multiple_headers(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this column header.",
                    "target": {"x": 20, "y": 50, "width": 360, "height": 28},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Name", "headeritem", (20, 50, 120, 28)),
                ControlCandidate("c002", "Status", "headeritem", (140, 50, 120, 28)),
                ControlCandidate("c003", "Owner", "headeritem", (260, 50, 120, 28)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_contextual_checkbox_row_highlights_single_checkbox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the checkbox in Task row.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 34, 20, 20)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (24, 34, 20, 20))

    def test_contextual_checkbox_row_rejects_multiple_checkboxes(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the checkbox in Task row.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 24, 20, 20)),
                ControlCandidate("c003", "Archived", "checkbox", (24, 52, 20, 20)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_model_rect_rejects_background_snap_when_foreground_is_plausible(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this button.",
                    "target": {"x": 120, "y": 136, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Save", "button", (120, 100, 80, 32), window_rank=0),
                ControlCandidate("c002", "Save", "button", (120, 145, 80, 32), window_rank=2),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rejected_reason, "ambiguous candidate snap")

    def test_model_rect_on_mismatched_candidate_rejects_instead_of_raw_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target
        from rect_snap import SnapResult

        model_rect = (120, 160, 80, 32)
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 120, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Cancel", "button", model_rect)],
            snapper=lambda rect, _instruction: SnapResult(
                rect=rect,
                confidence=0.41,
                source="model",
                matched_text="Cancel",
            ),
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.matched_text, "Cancel")
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_candidate_snapshot_miss_does_not_call_fresh_snapper(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        calls: list[bool] = []

        def snapper(_rect, _instruction):
            calls.append(True)
            raise AssertionError("fresh snapper should not run after candidate snapshot no-match")

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 420, "y": 420, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Cancel", "button", (120, 160, 80, 32))],
            snapper=snapper,
        )

        self.assertFalse(calls)
        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_rejected_fresh_snap_does_not_fall_back_to_raw_model_rect(self) -> None:
        from help_session import resolve_help_target
        from rect_snap import SnapResult

        model_rect = (120, 160, 80, 32)
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 120, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [],
            snapper=lambda _rect, _instruction: SnapResult(
                rect=model_rect,
                confidence=0.41,
                source="uia",
                matched_text="Cancel saveButton",
                rejected_reason="candidate semantic mismatch",
            ),
        )

        self.assertEqual(target.source, "snap")
        self.assertEqual(target.rect, model_rect)
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_fresh_snap_control_type_mismatch_does_not_fall_back_to_raw_model_rect(self) -> None:
        from help_session import resolve_help_target
        from rect_snap import snap_to_control

        button = _make_button("", 120, 160, 32, 32)
        window = _make_window("Settings", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this checkbox.",
                    "target": {"x": 120, "y": 160, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [],
            snapper=lambda rect, instruction: snap_to_control(
                rect,
                instruction,
                desktop_factory=lambda: desktop,
                timeout_ms=2000,
            ),
        )

        self.assertEqual(target.source, "snap")
        self.assertEqual(target.rect, (120, 160, 32, 32))
        self.assertEqual(target.rejected_reason, "control type mismatch")

    def test_oversized_model_rect_is_rejected(self) -> None:
        from help_session import resolve_help_target
        from rect_snap import SnapResult

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the button.",
                    "target": {"x": 100, "y": 100, "width": 600, "height": 300},
                }
            ),
            self._capture(),
            [],
            snapper=lambda rect, _instruction: SnapResult(
                rect=rect,
                confidence=0.0,
                source="model",
            ),
        )

        self.assertEqual(target.source, "model")
        self.assertEqual(target.rejected_reason, "oversized target")

    def test_partially_offscreen_candidate_is_clipped_before_display(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import clip_resolution_to_capture, resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Edge.",
                    "target_id": "c001",
                    "target": {"x": 0, "y": 120, "width": 40, "height": 30},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Edge", "button", (-20, 120, 60, 30))],
        )

        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (0, 120, 40, 30))

        raw_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Edge.",
                    "target_id": "c001",
                    "target": {"x": 0, "y": 120, "width": 40, "height": 30},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Edge", "button", (-20, 120, 60, 30))],
            clip_to_capture=False,
        )
        clipped = clip_resolution_to_capture(raw_target, self._capture())
        self.assertEqual(raw_target.rect, (-20, 120, 60, 30))
        self.assertEqual(clipped.rect, (0, 120, 40, 30))


class LooksOversizedTests(unittest.TestCase):
    def _make_decision(self, w: int, h: int):
        from agent import LiveHelpDecision

        return LiveHelpDecision(
            kind="step",
            instruction="placeholder",
            target_norm_x=100,
            target_norm_y=200,
            target_norm_width=w,
            target_norm_height=h,
        )

    def test_normal_button_not_oversized(self) -> None:
        from help_session import looks_oversized

        self.assertFalse(looks_oversized(self._make_decision(80, 30)))

    def test_wide_input_not_oversized(self) -> None:
        from help_session import looks_oversized

        self.assertFalse(looks_oversized(self._make_decision(300, 40)))

    def test_panel_sized_box_is_oversized_by_area(self) -> None:
        from help_session import looks_oversized

        self.assertTrue(looks_oversized(self._make_decision(400, 300)))

    def test_very_wide_strip_is_oversized_by_edge(self) -> None:
        from help_session import looks_oversized

        self.assertTrue(looks_oversized(self._make_decision(450, 30)))

    def test_very_tall_column_is_oversized_by_edge(self) -> None:
        from help_session import looks_oversized

        self.assertTrue(looks_oversized(self._make_decision(40, 450)))


if __name__ == "__main__":
    unittest.main()
