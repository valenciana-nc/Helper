# Codex model catalogue

Helper defaults to `gpt-6-luna` for computer use and reasoning. Explicit
`HELPER_AGENT_MODEL` and `HELPER_REASONING_MODEL` values override that default.

Settings → Providers & models uses the installed Codex model catalogue from
`CODEX_HOME/models_cache.json`, or `~/.codex/models_cache.json` by default.
Only picker-visible entries are offered. If that file is unavailable, current
offline suggestions are provided. Neither source guarantees account access.

The catalogue includes GPT-6.1 Sol, GPT-6 Astra, GPT-6 Sol, GPT-6 Luna, and
GPT-5.6 Sol/Terra/Luna. Display names appear in the picker; requests and saved
settings use the exact model ID. Models explicitly lacking image input are
excluded from the computer-use picker. Editable custom IDs remain supported.

**Refresh models** requests the signed-in account's catalogue from
`https://api.openai.com/v1/models` on a background thread with a bounded timeout.
It preserves server ordering and filters `visibility: "list"`, as specified in
[OpenAI's model-list guidance](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).
Refreshing makes no inference request and does not clear stored credentials.
Account changes invalidate earlier refresh results.

The existing Helper sign-in returned HTTP 403 during the catalogue update on
2026-10-18. Refresh therefore falls back to the installed catalogue and reports
that source and failure in Settings. The existing authentication and inference
endpoints have not been migrated as part of this catalogue update.

Existing configured models are preserved. Settings flags a model missing from
the catalogue; choose a current model and use **Save and restart** to apply it.
The update does not silently migrate a pinned model or change the other
providers' model menus.
