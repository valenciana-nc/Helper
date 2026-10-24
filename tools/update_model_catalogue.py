"""Regenerate Helper's offline chat-model snapshot from public metadata (no credentials).

Review provider docs and the resulting diff before shipping. Account-specific
availability is obtained separately through the desktop's Refresh models buttons.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re

import requests

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    'https://models.dev/api.json',
    'https://developers.openai.com/api/docs/models',
    'https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference',
    'https://docs.x.ai/developers/models',
    'https://docs.x.ai/developers/rest-api-reference/inference/models',
    'https://platform.claude.com/docs/en/about-claude/models/overview',
]
# Official OpenAI catalogue entries not covered by models.dev's current snapshot.
# These are text/vision Responses models; specialized media/tools APIs are omitted.
OPENAI_ADDITIONAL = {
    'gpt-5.6-cyber': 'GPT-5.6 Cyber (restricted access)',
    'gpt-5.2-codex': 'GPT-5.2 Codex',
    'gpt-5.1-codex-max': 'GPT-5.1 Codex Max',
    'gpt-5.1-codex': 'GPT-5.1 Codex',
    'gpt-5.1-codex-mini': 'GPT-5.1 Codex Mini',
    'gpt-5-codex': 'GPT-5 Codex',
}


def build_snapshot(payload: dict, checked: str) -> dict:
    providers = {}
    for source, target in (('openai', 'codex'), ('xai', 'xai'), ('anthropic', 'anthropic')):
        rows = payload[source]['models'].values()
        rows = sorted(rows, key=lambda row: (row.get('release_date', ''), row['id']), reverse=True)
        choices = []
        for row in rows:
            model = row['id']
            modalities = row.get('modalities', {})
            if (row.get('status') == 'retired' or row.get('release_date', '') > checked
                    or modalities.get('output') != ['text']):
                continue
            if source == 'openai' and (model in {'gpt-3.5-turbo', 'gpt-4', 'gpt-4-turbo'}
                                       or not re.match(r'^(gpt-|o[134])', model)):
                continue  # These legacy Chat Completions-only models cannot use Helper's Responses adapter.
            if source == 'xai' and not model.startswith('grok-'):
                continue
            if source == 'anthropic' and not model.startswith('claude-'):
                continue
            label = row.get('name', model)
            if row.get('status') == 'deprecated':
                label += ' (legacy)'
            choices.append({'id': model, 'label': label, 'image_input': 'image' in modalities.get('input', [])})
        if source == 'openai':
            seen = {row['id'] for row in choices}
            choices += [{'id': model, 'label': label, 'image_input': True}
                        for model, label in OPENAI_ADDITIONAL.items() if model not in seen]
        providers[target] = choices
    return {'checked_on': checked, 'sources': SOURCES, 'providers': providers}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='Use a previously downloaded public models.dev JSON response.')
    parser.add_argument('--date', default=date.today().isoformat())
    parser.add_argument('--output', type=Path, default=ROOT / 'assets' / 'model_catalogue.json')
    args = parser.parse_args()
    date.fromisoformat(args.date)
    if args.input:
        payload = json.loads(args.input.read_text(encoding='utf-8'))
    else:
        response = requests.get(SOURCES[0], timeout=(5, 30), allow_redirects=False)
        response.raise_for_status()
        payload = response.json()
    snapshot = build_snapshot(payload, args.date)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')
    for provider, choices in snapshot['providers'].items():
        print(f'{provider}: {len(choices)} chat models')
    print(f'Written: {args.output}. Review against the listed official sources before shipping.')


if __name__ == '__main__':
    main()
