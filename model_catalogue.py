"""Codex picker models, with an installed catalogue and account refresh."""
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


# Offline suggestions, checked against the current installed Codex catalogue.
# A listed suggestion is not a promise of access for a particular account.
BUNDLED_CODEX_MODELS = (
    ModelChoice('gpt-6.1-sol', 'GPT-6.1 Sol'),
    ModelChoice('gpt-6-astra', 'GPT-6 Astra'),
    ModelChoice('gpt-6-sol', 'GPT-6 Sol'),
    ModelChoice('gpt-6-luna', 'GPT-6 Luna'),
    ModelChoice('gpt-5.6-sol', 'GPT-5.6 Sol'),
    ModelChoice('gpt-5.6-terra', 'GPT-5.6 Terra'),
    ModelChoice('gpt-5.6-luna', 'GPT-5.6 Luna'),
)


def parse_catalogue(payload: object) -> tuple[ModelChoice, ...]:
    if not isinstance(payload, dict):
        return ()
    rows = payload.get('models')
    if not isinstance(rows, list):
        return ()
    choices = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get('visibility') != 'list' or row.get('hidden'):
            continue
        slug = row.get('slug')
        if not isinstance(slug, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', slug) or slug in seen:
            continue
        label = row.get('display_name')
        if not isinstance(label, str) or not label.strip():
            label = slug
        label = ' '.join(label.split())[:120]
        modalities = row.get('input_modalities', row.get('inputModalities'))
        image_input = not isinstance(modalities, list) or 'image' in modalities
        choices.append(ModelChoice(slug, label, image_input))
        seen.add(slug)
    return tuple(choices)


@lru_cache(maxsize=2)
def _read_installed_catalogue(path: str, modified_ns: int) -> tuple[ModelChoice, ...]:
    del modified_ns  # cache invalidation key; never import instructions from model entries
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
                return ModelCatalogue(choices, 'Installed Codex catalogue')
    except OSError:
        pass
    return ModelCatalogue(BUNDLED_CODEX_MODELS, 'Offline model suggestions')


def refresh_codex_catalogue() -> ModelCatalogue:
    """Read account choices without issuing inference or altering sign-in state."""
    import oauth_codex

    fallback = load_codex_catalogue()
    try:
        access = oauth_codex.get_access_token()
        with requests.get('https://api.openai.com/v1/models',
                          headers={'Authorization': f'Bearer {access}'},
                          timeout=(5, 12), allow_redirects=False) as response:
            if response.status_code != 200:
                return replace(fallback, warning=f'Live refresh unavailable (HTTP {response.status_code}).')
            choices = parse_catalogue(response.json())
            if not choices:
                return replace(fallback, warning='The account returned no picker models.')
            return ModelCatalogue(choices, 'Signed-in account catalogue')
    except oauth_codex.NotSignedIn:
        return replace(fallback, warning='Sign in to refresh account models.')
    except oauth_codex.LoginError:
        return replace(fallback, warning='Sign in again to refresh account models.')
    except ValueError:
        return replace(fallback, warning='The model catalogue response could not be read.')
    except requests.RequestException:
        return replace(fallback, warning='Live refresh could not reach the model catalogue.')
