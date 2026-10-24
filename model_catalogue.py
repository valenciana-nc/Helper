"""Dated offline model catalogues and read-only account refresh for Helper's providers."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import requests


@dataclass(frozen=True)
class ModelChoice:
    id: str
    label: str
    image_input: bool = True


@dataclass(frozen=True)
class ModelCatalogue:
    models: tuple[ModelChoice, ...]
    source: str
    warning: str = ''


CATALOGUE_PATH = Path(__file__).parent / 'assets' / 'model_catalogue.json'
CATALOGUE_PROVIDERS = ('codex', 'xai', 'anthropic')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}')
_FALLBACK = {
    'codex': (ModelChoice('gpt-6.1-sol', 'GPT-6.1 Sol'), ModelChoice('gpt-6-astra', 'GPT-6 Astra'),
              ModelChoice('gpt-6-luna', 'GPT-6 Luna')),
    'xai': (ModelChoice('grok-4.7', 'Grok 4.7'), ModelChoice('grok-4.6', 'Grok 4.6')),
    'anthropic': (ModelChoice('claude-opus-5-5', 'Claude Opus 5.5'),
                  ModelChoice('claude-sonnet-5-5', 'Claude Sonnet 5.5'),
                  ModelChoice('claude-haiku-5-5', 'Claude Haiku 5.5'),
                  ModelChoice('claude-fable-5-1', 'Claude Fable 5.1')),
}


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _label(value: object, model_id: str) -> str:
    return ' '.join(value.split())[:120] if isinstance(value, str) and value.strip() else model_id


def _compatible_id(provider: str, model_id: str) -> bool:
    if not _valid_id(model_id):
        return False
    if any(part in model_id.lower() for part in ('embedding', 'moderation', 'imagine', 'realtime', 'audio',
                                                'transcribe', 'tts', 'image', 'deep-research', 'search-preview',
                                                'computer-use-preview')):
        return False
    if provider == 'anthropic':
        return model_id.startswith('claude-')
    if provider == 'xai':
        return model_id.startswith('grok-') or model_id == 'latest'
    return model_id.startswith(('gpt-', 'o1', 'o3', 'o4', 'codex-')) and model_id not in {
        'gpt-3.5-turbo', 'gpt-4', 'gpt-4-turbo', 'gpt-4-turbo-preview'}


def _load_bundled() -> tuple[str, dict[str, tuple[ModelChoice, ...]]]:
    try:
        if CATALOGUE_PATH.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('oversized model snapshot')
        payload = json.loads(CATALOGUE_PATH.read_text(encoding='utf-8'))
        checked = payload['checked_on']
        if not isinstance(checked, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', checked):
            raise ValueError('invalid snapshot date')
        providers = {}
        for provider in CATALOGUE_PROVIDERS:
            seen = set()
            choices = []
            for row in payload['providers'][provider]:
                if not isinstance(row, dict):
                    continue
                model = row.get('id')
                if not isinstance(model, str) or not _compatible_id(provider, model) or model in seen:
                    continue
                choices.append(ModelChoice(model, _label(row.get('label'), model), row.get('image_input') is True))
                seen.add(model)
            providers[provider] = tuple(choices) or _FALLBACK[provider]
        return checked, providers
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return 'offline fallback', _FALLBACK


BUNDLED_CHECKED_ON, BUNDLED_PROVIDER_MODELS = _load_bundled()
BUNDLED_CODEX_MODELS = BUNDLED_PROVIDER_MODELS['codex']


def _image_input(row: dict, known: ModelChoice | None) -> bool:
    modalities = row.get('input_modalities', row.get('inputModalities'))
    if isinstance(modalities, list):
        return 'image' in modalities
    capabilities = row.get('capabilities')
    if isinstance(capabilities, dict):
        image = capabilities.get('image_input')
        if isinstance(image, dict) and isinstance(image.get('supported'), bool):
            return image['supported']
    # An unknown model without capability metadata is chat-only until verified.
    return known.image_input if known else False


def parse_catalogue(payload: object) -> tuple[ModelChoice, ...]:
    """OpenAI's ChatGPT account picker response, not the generic API-key model list."""
    if not isinstance(payload, dict) or not isinstance(payload.get('models'), list):
        return ()
    known = {choice.id: choice for choice in BUNDLED_CODEX_MODELS}
    choices = []
    seen = set()
    for row in payload['models'][:4000]:
        if not isinstance(row, dict) or row.get('visibility') != 'list' or row.get('hidden'):
            continue
        slug = row.get('slug')
        if not _valid_id(slug) or slug in seen:
            continue
        choices.append(ModelChoice(slug, _label(row.get('display_name'), slug), _image_input(row, known.get(slug))))
        seen.add(slug)
    return tuple(choices)


def parse_provider_catalogue(provider: str, payload: object) -> tuple[ModelChoice, ...]:
    if provider == 'codex':
        return parse_catalogue(payload)
    if provider not in {'xai', 'anthropic'} or not isinstance(payload, dict):
        return ()
    rows = payload.get('models') if provider == 'xai' else payload.get('data')
    if not isinstance(rows, list):
        return ()
    known = {choice.id: choice for choice in BUNDLED_PROVIDER_MODELS[provider]}
    choices = []
    seen = set()
    for row in rows[:4000]:
        if not isinstance(row, dict) or row.get('lifecycle', row.get('status')) == 'retired' or row.get('hidden'):
            continue
        outputs = row.get('output_modalities')
        if isinstance(outputs, list) and outputs != ['text']:
            continue
        model_id = row.get('id')
        if not isinstance(model_id, str) or not _compatible_id(provider, model_id):
            continue
        ids = [model_id]
        if provider == 'xai' and isinstance(row.get('aliases'), list):
            ids.extend(row['aliases'])
        for model in ids:
            if not isinstance(model, str) or not _compatible_id(provider, model) or model in seen:
                continue
            label = _label(row.get('display_name'), model) if model == model_id else model
            if row.get('lifecycle', row.get('status')) == 'deprecated':
                label += ' (legacy)'
            choices.append(ModelChoice(model, label, _image_input(row, known.get(model) or known.get(model_id))))
            seen.add(model)
    return tuple(choices)


@lru_cache(maxsize=2)
def _read_installed_catalogue(path: str, modified_ns: int) -> tuple[ModelChoice, ...]:
    del modified_ns  # cache invalidation key; never import model instructions
    try:
        return parse_catalogue(json.loads(Path(path).read_text(encoding='utf-8')))
    except (OSError, ValueError, UnicodeError):
        return ()


def load_codex_catalogue() -> ModelCatalogue:
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    path = home / 'models_cache.json'
    try:
        stat = path.stat()
        if stat.st_size <= 8 * 1024 * 1024:
            choices = _read_installed_catalogue(str(path), stat.st_mtime_ns)
            if choices:
                seen = {choice.id for choice in choices}
                combined = choices + tuple(model for model in BUNDLED_CODEX_MODELS if model.id not in seen)
                return ModelCatalogue(combined, f'Installed + bundled OpenAI catalogue ({BUNDLED_CHECKED_ON})')
    except OSError:
        pass
    return ModelCatalogue(BUNDLED_CODEX_MODELS, f'Bundled public catalogue ({BUNDLED_CHECKED_ON})')


def load_provider_catalogue(provider: str) -> ModelCatalogue:
    if provider == 'codex':
        return load_codex_catalogue()
    if provider not in BUNDLED_PROVIDER_MODELS:
        raise ValueError('No built-in model catalogue for this provider.')
    return ModelCatalogue(BUNDLED_PROVIDER_MODELS[provider], f'Bundled public catalogue ({BUNDLED_CHECKED_ON})')


def refresh_codex_catalogue(fallback: ModelCatalogue | None = None) -> ModelCatalogue:
    """List account choices without inference or clearing credentials on model-list errors."""
    import oauth_codex

    fallback = fallback or load_codex_catalogue()
    try:
        access = oauth_codex.get_access_token()
        with requests.get('https://api.openai.com/v1/models', headers={'Authorization': f'Bearer {access}'},
                          timeout=(5, 12), allow_redirects=False) as response:
            if response.status_code != 200:
                return replace(fallback, warning=f'Live refresh unavailable (HTTP {response.status_code}); keeping previous models.')
            choices = parse_catalogue(response.json())
            if not choices:
                return replace(fallback, warning='The account returned no picker models; keeping previous models.')
            return ModelCatalogue(choices, 'Signed-in account catalogue')
    except oauth_codex.NotSignedIn:
        return replace(fallback, warning='Sign in to refresh account models.')
    except oauth_codex.LoginError:
        return replace(fallback, warning='Sign in again to refresh account models.')
    except ValueError:
        return replace(fallback, warning='The model catalogue response could not be read; keeping previous models.')
    except requests.RequestException:
        return replace(fallback, warning='Live refresh could not reach the model catalogue; keeping previous models.')


def refresh_provider_catalogue(provider: str, *, api_key: str = '', auth_mode: str = 'auto',
                               fallback: ModelCatalogue | None = None) -> ModelCatalogue:
    """Refresh only fixed, official provider endpoints using the explicitly selected connection."""
    fallback = fallback or load_provider_catalogue(provider)
    if provider == 'codex':
        return refresh_codex_catalogue(fallback)
    if provider not in {'xai', 'anthropic'}:
        raise ValueError('No built-in model catalogue for this provider.')
    import oauth_anthropic
    import oauth_xai

    auth = oauth_xai if provider == 'xai' else oauth_anthropic
    auth_mode = auth_mode.strip().lower()
    subscription = auth_mode == 'oauth' or (auth_mode != 'api_key' and not api_key)
    try:
        token = auth.get_access_token() if subscription else api_key.strip()
        if not token:
            return replace(fallback, warning='Connect this provider in Account before refreshing models.')
        headers = {'Accept': 'application/json'}
        if provider == 'xai':
            url = 'https://api.x.ai/v1/language-models'
            headers['Authorization'] = f'Bearer {token}'
        else:
            url = 'https://api.anthropic.com/v1/models'
            headers['anthropic-version'] = '2023-06-01'
            if subscription:
                headers.update({'Authorization': f'Bearer {token}', 'anthropic-beta': 'oauth-2025-04-20',
                                'user-agent': 'claude-cli/2.1.280', 'x-app': 'cli'})
            else:
                headers['x-api-key'] = token
        rows = []
        cursor = None
        seen_cursors = set()
        for _ in range(10):  # bounded pagination; never follow a server-supplied URL
            params = {'limit': 1000} if provider == 'anthropic' else {}
            if cursor:
                params['after_id'] = cursor
            with requests.get(url, headers=headers, params=params, timeout=(5, 12), allow_redirects=False) as response:
                if response.status_code != 200:
                    return replace(fallback, warning=f'Live refresh unavailable (HTTP {response.status_code}); keeping previous models.')
                payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError('Invalid model list')
            page = payload.get('data' if provider == 'anthropic' else 'models')
            if not isinstance(page, list):
                raise ValueError('Invalid model list')
            rows.extend(page)
            if provider != 'anthropic' or not payload.get('has_more'):
                break
            cursor = payload.get('last_id')
            if not _valid_id(cursor) or cursor in seen_cursors:
                raise ValueError('Invalid model cursor')
            seen_cursors.add(cursor)
        else:
            raise ValueError('Too many model pages')
        choices = parse_provider_catalogue(provider, {'data' if provider == 'anthropic' else 'models': rows})
        if not choices:
            return replace(fallback, warning='The account returned no compatible chat models; keeping previous models.')
        return ModelCatalogue(choices, 'Signed-in account catalogue' if subscription else 'API-key account catalogue')
    except auth.LoginError:
        return replace(fallback, warning='Sign in again in Account to refresh models; keeping previous models.')
    except ValueError:
        return replace(fallback, warning='The model catalogue response could not be read; keeping previous models.')
    except requests.RequestException:
        return replace(fallback, warning='Live refresh could not reach the model catalogue; keeping previous models.')
