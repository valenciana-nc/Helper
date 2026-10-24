"""Provider model snapshots, account-list APIs and native picker refresh without live credentials."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtWidgets import QApplication
import requests

import config
from env_io import read_env, write_env
import model_catalogue as models
import oauth_anthropic
import oauth_codex
import oauth_xai
from tools.update_model_catalogue import build_snapshot
from ui_dashboard import DashboardWindow


def response(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result._content_consumed = True
    return result


def codex_payload(model='gpt-account-new'):
    return {'models': [{'slug': model, 'display_name': 'Account model', 'visibility': 'list',
                        'input_modalities': ['text', 'image']}]}


def xai_payload(model='grok-account-new'):
    return {'models': [{'id': model, 'input_modalities': ['text', 'image'], 'output_modalities': ['text']}]}


class ModelCatalogueTests(unittest.TestCase):
    def test_snapshot_contains_current_provider_lineups_and_exact_ids(self):
        self.assertEqual(models.BUNDLED_CHECKED_ON, '2026-10-23')
        required = {
            'codex': {'gpt-6.1-sol', 'gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna', 'gpt-5.6-terra',
                      'gpt-5.5-pro', 'gpt-5.4-mini', 'gpt-5.3-codex', 'o3-mini'},
            'xai': {'grok-4.7', 'grok-4.6', 'grok-4.5', 'grok-4.3', 'grok-build-0.1'},
            'anthropic': {'claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-fable-5-1',
                          'claude-opus-4-8', 'claude-sonnet-4-6', 'claude-haiku-4-5-20251001'},
        }
        for provider, expected in required.items():
            choices = models.load_provider_catalogue(provider).models
            ids = [choice.id for choice in choices]
            with self.subTest(provider=provider):
                self.assertTrue(expected <= set(ids))
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(all(models._valid_id(model) for model in ids))
                self.assertFalse(any(part in model for model in ids for part in ('imagine', 'embedding', 'realtime', 'tts')))
                self.assertTrue(expected <= set(config.PROVIDER_MODELS[provider]))
        self.assertFalse(next(choice for choice in models.BUNDLED_CODEX_MODELS if choice.id == 'o3-mini').image_input)

    def test_snapshot_generator_excludes_future_media_and_retired_models(self):
        payload = {provider: {'models': {}} for provider in ('openai', 'xai', 'anthropic')}
        rows = payload['xai']['models']
        for model, output, release, status in (
                ('grok-current', ['text'], '2026-10-01', None), ('grok-future', ['text'], '2027-01-01', None),
                ('grok-retired', ['text'], '2026-01-01', 'retired'), ('grok-imagine-test', ['image'], '2026-01-01', None)):
            rows[model] = {'id': model, 'modalities': {'input': ['text', 'image'], 'output': output},
                           'release_date': release, 'status': status}
        built = build_snapshot(payload, '2026-10-23')
        self.assertEqual([row['id'] for row in built['providers']['xai']], ['grok-current'])

    def test_missing_corrupt_and_stale_installed_codex_catalogue_keep_current_bundled_models(self):
        with TemporaryDirectory() as folder, patch.dict('os.environ', {'CODEX_HOME': folder}):
            path = Path(folder) / 'models_cache.json'
            self.assertIn('Bundled', models.load_codex_catalogue().source)
            path.write_text('bad-json', encoding='utf-8')
            self.assertIn('Bundled', models.load_codex_catalogue().source)
            path.write_text(json.dumps(codex_payload('gpt-installed')), encoding='utf-8')
            catalogue = models.load_codex_catalogue()
            self.assertEqual(catalogue.models[0].id, 'gpt-installed')
            self.assertIn('gpt-6.1-sol', {choice.id for choice in catalogue.models})
            self.assertIn('Installed + bundled', catalogue.source)

    def test_openai_parser_obeys_visibility_deduplication_sanitizes_ids_and_ignores_instructions(self):
        rows = codex_payload()['models']
        rows += [{'slug': 'gpt-hidden', 'visibility': 'list', 'hidden': True},
                 {'slug': 'gpt-internal', 'visibility': 'internal'}, {'slug': 'bad\nmodel', 'visibility': 'list'},
                 rows[0], {'slug': 'gpt-text-only', 'display_name': '  Text\n only ', 'visibility': 'list',
                           'input_modalities': ['text'], 'instructions': 'never import this'},
                 {'slug': 'gpt-unknown', 'visibility': 'list'}]
        choices = models.parse_catalogue({'models': rows})
        self.assertEqual([choice.id for choice in choices], ['gpt-account-new', 'gpt-text-only', 'gpt-unknown'])
        self.assertEqual(choices[1].label, 'Text only')
        self.assertFalse(choices[1].image_input)
        self.assertFalse(choices[2].image_input)
        self.assertEqual(models.parse_catalogue({'data': rows}), ())

    def test_xai_parser_uses_language_modalities_and_aliases_not_media_or_retired_models(self):
        payload = xai_payload()
        payload['models'][0]['aliases'] = ['grok-new-latest', 'grok-account-new', 'bad\nheader', 7]
        payload['models'] += [
            {'id': 'grok-imagine-image', 'input_modalities': ['text'], 'output_modalities': ['image']},
            {'id': 'grok-old', 'status': 'retired'},
            {'id': 'grok-text', 'input_modalities': ['text'], 'output_modalities': ['text']},
        ]
        choices = models.parse_provider_catalogue('xai', payload)
        self.assertEqual([choice.id for choice in choices], ['grok-account-new', 'grok-new-latest', 'grok-text'])
        self.assertTrue(choices[1].image_input)
        self.assertFalse(choices[2].image_input)

    def test_anthropic_parser_reads_capabilities_legacy_lifecycle_and_unknown_vision(self):
        choices = models.parse_provider_catalogue('anthropic', {'data': [
            {'id': 'claude-new', 'display_name': 'Claude New', 'capabilities': {'image_input': {'supported': True}}},
            {'id': 'claude-retired', 'lifecycle': 'retired'},
            {'id': 'claude-legacy', 'lifecycle': 'deprecated', 'capabilities': {'image_input': {'supported': False}}},
            {'id': 'claude-unknown'}, {'id': 'not-claude'},
        ]})
        self.assertEqual([choice.id for choice in choices], ['claude-new', 'claude-legacy', 'claude-unknown'])
        self.assertTrue(choices[0].image_input)
        self.assertFalse(choices[1].image_input)
        self.assertIn('legacy', choices[1].label)
        self.assertFalse(choices[2].image_input)

    def test_codex_refresh_is_read_only_and_account_choices_are_authoritative(self):
        with (patch.object(oauth_codex, 'get_access_token', return_value='fixture-access'),
              patch.object(models.requests, 'get', return_value=response(codex_payload())) as get,
              patch('token_store.clear') as clear, patch.object(models.requests, 'post') as post):
            catalogue = models.refresh_provider_catalogue('codex')
        self.assertEqual([choice.id for choice in catalogue.models], ['gpt-account-new'])
        self.assertEqual(catalogue.source, 'Signed-in account catalogue')
        self.assertEqual(get.call_args.args[0], 'https://api.openai.com/v1/models')
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer fixture-access')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        clear.assert_not_called()
        post.assert_not_called()

    def test_xai_refresh_uses_selected_subscription_or_api_key_without_fallback(self):
        for mode, token in (('oauth', 'subscription-fixture'), (' OAuth ', 'subscription-fixture'),
                            ('api_key', 'api-fixture')):
            with (self.subTest(mode=mode), patch.object(oauth_xai, 'get_access_token', return_value='subscription-fixture') as auth,
                  patch.object(models.requests, 'get', return_value=response(xai_payload())) as get):
                catalogue = models.refresh_provider_catalogue('xai', api_key='api-fixture', auth_mode=mode)
            self.assertEqual(get.call_args.args[0], 'https://api.x.ai/v1/language-models')
            self.assertEqual(get.call_args.kwargs['headers']['Authorization'], f'Bearer {token}')
            self.assertEqual(auth.call_count, int(mode.strip().lower() == 'oauth'))
            self.assertEqual([choice.id for choice in catalogue.models], ['grok-account-new'])
        with (patch.object(oauth_xai, 'get_access_token', side_effect=oauth_xai.NotSignedIn('private')),
              patch.object(models.requests, 'get') as get):
            result = models.refresh_provider_catalogue('xai', api_key='do-not-fallback', auth_mode='oauth')
        get.assert_not_called()
        self.assertNotIn('private', result.warning)
        self.assertTrue(result.warning)

    def test_anthropic_refresh_paginates_safely_and_uses_correct_auth_headers(self):
        for mode in ('oauth', 'api_key'):
            pages = [response({'data': [{'id': 'claude-first'}], 'has_more': True, 'last_id': 'claude-first'}),
                     response({'data': [{'id': 'claude-second'}], 'has_more': False})]
            with (self.subTest(mode=mode), patch.object(oauth_anthropic, 'get_access_token', return_value='oauth-fixture') as auth,
                  patch.object(models.requests, 'get', side_effect=pages) as get):
                result = models.refresh_provider_catalogue('anthropic', api_key='key-fixture', auth_mode=mode)
            self.assertEqual([choice.id for choice in result.models], ['claude-first', 'claude-second'])
            self.assertEqual(get.call_count, 2)
            for args in get.call_args_list:
                self.assertEqual(args.args[0], 'https://api.anthropic.com/v1/models')
                self.assertFalse(args.kwargs['allow_redirects'])
            self.assertEqual(get.call_args_list[1].kwargs['params'], {'limit': 1000, 'after_id': 'claude-first'})
            headers = get.call_args.kwargs['headers']
            self.assertEqual(headers['anthropic-version'], '2023-06-01')
            self.assertEqual(headers.get('Authorization'), 'Bearer oauth-fixture' if mode == 'oauth' else None)
            self.assertEqual(headers.get('x-api-key'), 'key-fixture' if mode == 'api_key' else None)
            self.assertEqual(auth.call_count, int(mode == 'oauth'))

    def test_refresh_failure_keeps_previous_models_and_never_erases_credentials(self):
        previous = models.ModelCatalogue((models.ModelChoice('grok-previous', 'Previous'),), 'Previous source')
        cases = [response({}, 403), response({}, 429), response({'models': []}), response([])]
        invalid = response({})
        invalid._content = b'not JSON'
        cases.append(invalid)
        for item in cases:
            with (self.subTest(status=item.status_code), patch.object(models.requests, 'get', return_value=item),
                  patch.object(oauth_xai, 'sign_out') as sign_out):
                result = models.refresh_provider_catalogue('xai', api_key='fixture', auth_mode='api_key', fallback=previous)
            self.assertEqual(result.models, previous.models)
            self.assertEqual(result.source, previous.source)
            self.assertTrue(result.warning)
            sign_out.assert_not_called()
        with patch.object(models.requests, 'get', side_effect=requests.Timeout('private')):
            result = models.refresh_provider_catalogue('xai', api_key='fixture', auth_mode='api_key', fallback=previous)
        self.assertEqual(result.models, previous.models)
        self.assertNotIn('private', result.warning)

    def test_missing_key_and_repeated_pagination_cursor_do_not_use_other_connections(self):
        with patch.object(models.requests, 'get') as get, patch.object(oauth_xai, 'get_access_token') as token:
            result = models.refresh_provider_catalogue('xai', auth_mode='api_key')
        get.assert_not_called()
        token.assert_not_called()
        self.assertTrue(result.warning)
        page = {'data': [{'id': 'claude-new'}], 'has_more': True, 'last_id': 'claude-new'}
        with patch.object(models.requests, 'get', side_effect=lambda *_a, **_k: response(page)) as get:
            result = models.refresh_provider_catalogue('anthropic', api_key='fixture', auth_mode='api_key')
        self.assertEqual(get.call_count, 2)
        self.assertTrue(result.warning)
        self.assertNotIn('claude-new', {choice.id for choice in result.models})


class ModelCatalogueUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    @contextmanager
    def dashboard(self, values=None):
        with (TemporaryDirectory() as folder, patch.object(oauth_codex, 'is_signed_in', return_value=False),
              patch.object(oauth_codex, 'account_email', return_value=None),
              patch.object(oauth_xai, 'is_signed_in', return_value=False),
              patch.object(oauth_anthropic, 'is_signed_in', return_value=False)):
            path = Path(folder) / '.env'
            if values:
                write_env(path, values)
            restart = Mock()
            window = DashboardWindow(path, lambda: {}, restart)
            try:
                yield window, path, restart
            finally:
                window.close()
                window.deleteLater()
                self.qt.processEvents()

    def select(self, window, provider):
        combo = window._field_widgets['HELPER_PROVIDER']
        combo.setCurrentIndex(combo.findData(provider))

    def wait_refresh(self, window):
        deadline = time.monotonic() + 2
        while window._catalogue_loading and time.monotonic() < deadline:
            self.qt.processEvents()
            time.sleep(0.01)
        self.qt.processEvents()
        self.assertFalse(window._catalogue_loading)

    def test_three_providers_get_current_menus_exact_ids_and_refresh_without_startup_network(self):
        with patch.object(models.requests, 'get') as get, self.dashboard() as (window, _, _):
            for provider, expected in (('codex', 'gpt-6.1-sol'), ('xai', 'grok-4.6'), ('anthropic', 'claude-sonnet-5-5')):
                self.select(window, provider)
                combo = window._field_widgets['HELPER_AGENT_MODEL']
                self.assertGreaterEqual(combo.findData(expected), 0)
                combo.setCurrentIndex(combo.findData(expected))
                self.assertEqual(window._field_value('HELPER_AGENT_MODEL'), expected)
                self.assertTrue(window._refresh_models_button.isEnabled())
                self.assertIn('2026-10-23', window._model_catalogue_hint.text())
            get.assert_not_called()

    def test_text_only_models_are_reasoning_choices_not_computer_use_choices(self):
        with self.dashboard() as (window, _, _):
            self.select(window, 'codex')
            self.assertEqual(window._field_widgets['HELPER_AGENT_MODEL'].findData('o3-mini'), -1)
            self.assertGreaterEqual(window._field_widgets['HELPER_REASONING_MODEL'].findData('o3-mini'), 0)
            card = window._provider_account_cards['xai']
            card.model_input.setCurrentText('grok-custom-draft')
            card.set_model_catalogue(models.ModelCatalogue((models.ModelChoice('grok-text', 'Text', False),
                                                           models.ModelChoice('grok-vision', 'Vision', True)), 'Test'))
            self.assertEqual(card.model_input.findText('grok-text'), -1)
            self.assertGreaterEqual(card.model_input.findText('grok-vision'), 0)
            self.assertEqual(card.model_input.currentText(), 'grok-custom-draft')

    def test_account_refresh_uses_saved_connection_not_drafts_and_updates_settings_and_card(self):
        values = {'HELPER_PROVIDER': 'xai', 'HELPER_XAI_API_KEY': 'saved-fixture', 'HELPER_XAI_AUTH_MODE': 'api_key'}
        with self.dashboard(values) as (window, path, restart):
            card = window._provider_account_cards['xai']
            card.key_input.setText('unsaved-private-draft')
            card.model_input.setCurrentText('grok-custom-draft')
            result = models.ModelCatalogue((models.ModelChoice('grok-refreshed', 'Grok Refreshed'),), 'API-key account catalogue')
            with patch('ui_dashboard.refresh_provider_catalogue', return_value=result) as refresh:
                card.refresh_models_button.click()
                self.wait_refresh(window)
            self.assertEqual(refresh.call_args.args, ('xai',))
            self.assertEqual(refresh.call_args.kwargs['api_key'], 'saved-fixture')
            self.assertEqual(refresh.call_args.kwargs['auth_mode'], 'api_key')
            self.assertGreaterEqual(card.model_input.findText('grok-refreshed'), 0)
            self.assertGreaterEqual(window._field_widgets['HELPER_AGENT_MODEL'].findData('grok-refreshed'), 0)
            self.assertEqual(card.model_input.currentText(), 'grok-custom-draft')
            self.assertEqual(read_env(path), values)
            restart.assert_not_called()

    def test_openai_refresh_never_receives_another_providers_saved_api_key(self):
        with self.dashboard({'HELPER_ANTHROPIC_API_KEY': 'unrelated-fixture'}) as (window, _, _):
            result = models.ModelCatalogue((models.ModelChoice('gpt-refreshed', 'Refreshed'),), 'Signed-in account catalogue')
            with patch('ui_dashboard.refresh_provider_catalogue', return_value=result) as refresh:
                window._refresh_models('codex')
                self.wait_refresh(window)
            self.assertEqual(refresh.call_args.args, ('codex',))
            self.assertNotIn('api_key', refresh.call_args.kwargs)
            self.assertNotIn('auth_mode', refresh.call_args.kwargs)

    def test_provider_switch_during_refresh_cannot_publish_models_into_the_wrong_menu(self):
        with self.dashboard() as (window, _, _):
            self.select(window, 'xai')
            with patch('ui_dashboard.Thread'):
                window._refresh_models()
            generation = window._catalogue_generation
            self.assertFalse(window._provider_account_cards['xai'].refresh_models_button.isEnabled())
            self.select(window, 'anthropic')
            result = models.ModelCatalogue((models.ModelChoice('grok-live', 'Grok Live'),), 'Signed-in account catalogue')
            window._on_catalogue_refreshed((generation, 'xai', result))
            combo = window._field_widgets['HELPER_AGENT_MODEL']
            self.assertEqual(combo.findData('grok-live'), -1)
            self.assertGreaterEqual(combo.findData('claude-opus-5-5'), 0)
            self.assertGreaterEqual(window._provider_account_cards['xai'].model_input.findText('grok-live'), 0)

    def test_credential_changes_invalidate_inflight_results_but_preserve_model_drafts(self):
        with self.dashboard() as (window, _, _):
            card = window._provider_account_cards['xai']
            card.model_input.setCurrentText('grok-draft')
            with patch('ui_dashboard.Thread'):
                window._refresh_models('xai')
            generation = window._catalogue_generation
            window._invalidate_model_catalogue('xai')
            result = models.ModelCatalogue((models.ModelChoice('grok-other-account', 'Other account'),), 'Signed-in account catalogue')
            window._on_catalogue_refreshed((generation, 'xai', result))
            self.assertEqual(card.model_input.findText('grok-other-account'), -1)
            self.assertEqual(card.model_input.currentText(), 'grok-draft')
            self.assertTrue(card.refresh_models_button.isEnabled())

    def test_failed_background_refresh_keeps_models_and_shows_warning(self):
        with self.dashboard() as (window, path, restart):
            previous = window._model_catalogues['anthropic']
            with patch('ui_dashboard.refresh_provider_catalogue', side_effect=RuntimeError('private')):
                window._provider_account_cards['anthropic'].refresh_models_button.click()
                self.wait_refresh(window)
            result = window._model_catalogues['anthropic']
            self.assertEqual(result.models, previous.models)
            self.assertIn('keeping', result.warning)
            self.assertNotIn('private', result.warning)
            self.assertFalse(path.exists())
            restart.assert_not_called()


if __name__ == '__main__':
    unittest.main()
