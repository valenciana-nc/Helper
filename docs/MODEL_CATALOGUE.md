# OpenAI, Grok, and Claude model catalogues

Helper ships a public model snapshot checked on **2026-10-23**, with:

| Provider | Bundled chat entries | Current families included |
|---|---:|---|
| OpenAI / ChatGPT sign-in | 45 | GPT-6.1 Sol, GPT-6 Astra/Sol/Luna, GPT-5.6 and earlier GPT/Codex/o-series variants |
| xAI / Grok | 8 | Grok 4.7, 4.6, 4.5, 4.3, Build, and 4.20 variants |
| Anthropic / Claude | 17 | Opus/Sonnet/Haiku 5.5, Fable 5.1, earlier supported families, aliases and dated snapshots |

These are **catalogue entries, not guaranteed account entitlements**. Public API
availability is not the same as subscription or endpoint access. Restricted and
legacy models may be unavailable to a particular account. Some specialized
language variants require provider-specific endpoint/options support; listing
one does not add that support to Helper's existing adapters.

Audio, realtime voice, image/video generation, embeddings, moderation, deep
research, and provider-native computer-tool models are not ordinary chat models
for Helper's current adapters and are excluded from these menus. Text-only chat
models appear in **Reasoning model**, not **Computer-use model**. Models without
known image capabilities are not newly offered for computer use.

## Choose a model

In **Settings → Providers & models**, select OpenAI/ChatGPT, Grok, or Claude, then
choose a computer-use and reasoning model. Friendly names are displayed, while
requests and saved settings use exact IDs. The Claude and Grok Account cards
also show the expanded, image-capable choices; their tooltips show model names.

Existing defaults and pinned/custom IDs are preserved. No more expensive model
is silently selected. Save and restart to apply a model change. A saved ID that
is absent from the current catalogue is retained and called out in Settings.

The OpenAI offline list combines any picker-visible installed Codex entries from
`CODEX_HOME/models_cache.json` (or `~/.codex/models_cache.json`) with the dated
public snapshot, so an old installed cache cannot hide newer models.

## Refresh account models

**Refresh models** works for all three providers in Settings. Claude and Grok
also have the button beside their model field in Account. Choose/save your
connection first: refresh uses the saved connection, never an unsaved key draft.
Model listing makes no inference request and does not switch providers, change
saved model settings, or clear sign-in on an HTTP model-list error.

| Provider | Official endpoint and behavior |
|---|---|
| OpenAI sign-in | `GET https://api.openai.com/v1/models`; use the `models` array, filter `visibility: "list"`, and preserve server ordering. |
| xAI | `GET https://api.x.ai/v1/language-models`; read input/output modalities and include valid returned aliases. |
| Anthropic | `GET https://api.anthropic.com/v1/models`; support pagination and image capabilities, exclude retired entries, label deprecated ones as legacy. |

Grok and Claude use the explicitly selected OAuth grant or API key. There is no
fallback from a failed subscription login to billed API access. Token renewal
can occur when needed; a revoked grant still requires signing in again.

The request runs on a background thread with bounded timeouts/pagination.
Successful account results replace public suggestions for that provider in the
running app, in both Settings and its Account card. Failures keep the previous
choices and display a warning. Switching providers while refreshing cannot put
one provider's results into another provider's menu. Credential changes discard
older in-flight results. Live lists are session-local and refresh again after
restart; selected model IDs remain saved.

This update does not migrate authentication or inference endpoints. In
particular, OpenAI's current ChatGPT-plan documentation describes a public
Responses endpoint, while Helper's existing Codex inference adapter retains its
previous route. A public catalogue entry is not proof that route accepts it.

## Sources and maintenance

The checked snapshot is [`assets/model_catalogue.json`](../assets/model_catalogue.json).
It contains only IDs, display labels, image support, a date, and source URLs;
provider-supplied instructions or code are never imported.

Sources checked:

- [OpenAI model catalogue](https://developers.openai.com/api/docs/models)
- [OpenAI account model-list guidance](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [xAI models](https://docs.x.ai/developers/models) and [Models API](https://docs.x.ai/developers/rest-api-reference/inference/models)
- [Claude models overview](https://platform.claude.com/docs/en/about-claude/models/overview) and [Models API](https://platform.claude.com/docs/en/api/models/list)
- [models.dev public metadata](https://models.dev/api.json), including older aliases, dates, and modalities

Regenerate public metadata without credentials, then review the diff against
the provider documentation before shipping:

```powershell
.\.venv\Scripts\python.exe tools/update_model_catalogue.py
```

The generator filters future-dated releases, retired entries, and non-text
output models. `tests/test_model_catalogue.py` tests parsing, refresh errors,
auth-mode isolation, pagination, capability filtering, exact-ID persistence,
provider switches, and stale-result rejection with fake HTTP/credentials.
Neither the snapshot nor tests prove live inference access for every entry.
