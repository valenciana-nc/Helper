# Native Claude subscription sign-in

Helper implements the browser and copy-code OAuth flows used by Pi for Claude
Pro/Max, directly in Python. It does not launch Pi or Claude Code, import their
tokens, or read browser cookies.

**Compatibility caveat:** this uses Claude Code's public OAuth client and the
Claude Code request format used by Pi, not a separately registered or officially
endorsed Helper integration. The approval page may identify Claude Code.
Anthropic can restrict third-party clients, account eligibility, models, or
usage. A successful sign-in does not guarantee that inference will be accepted.
An ordinary API key remains an explicit alternative with separate API billing.

## Connect

1. Restart Helper after installing the changes. Open **Account → Claude**.
2. Leave **Login method → Browser callback**, then click **Sign in with Claude**.
3. Approve in your browser. If the callback cannot reach Helper, paste the final
   redirect URL into the masked authorization input and click **Submit code**.
4. Alternatively, select **Copy code (manual)** before signing in. Paste the
   `code#state` shown by Claude after browser approval.
5. Choose a model, then **Use subscription and restart**.

The card shows the authorization address if a browser cannot open. Cancellation,
closing the dashboard, or quitting stops pending login; an in-flight HTTP call
may finish before cancellation is observed. The local callback listener closes
on success, failure, cancellation, and timeout.

## How Pi does it

Reference snapshot: [`anthropic.ts`](https://github.com/earendil-works/pi/blob/136b87e/packages/ai/src/auth/oauth/anthropic.ts)
and [`anthropic-messages.ts`](https://github.com/earendil-works/pi/blob/136b87e/packages/ai/src/api/anthropic-messages.ts).

- Authorization URL: `https://claude.ai/oauth/authorize`.
- Token exchange/refresh: `https://platform.claude.com/v1/oauth/token`, using JSON.
- Public client ID: `9d1c250a-e61b-44d9-88ed-5944d1962f5e` (not a secret).
- PKCE: random verifier, SHA-256 challenge, `S256`; state matches the verifier.
- Browser redirect: `http://localhost:53692/callback`; bind only `127.0.0.1`.
  If occupied, use a free loopback port. If binding fails entirely, manually
  pasting the final redirect URL still works.
- Copy-code redirect: `https://platform.claude.com/oauth/code/callback`.
- Scopes match Pi: `org:create_api_key user:profile user:inference
  user:sessions:claude_code user:mcp_servers user:file_upload`. Helper does not
  create an API key; authorization is still subject to the permissions shown
  on the provider's approval page.
- Inference goes to `https://api.anthropic.com/v1/messages` with bearer auth,
  `claude-code-20250219,oauth-2025-04-20` beta headers, `x-app: cli`, and Pi's
  pinned `claude-cli/2.1.280` compatibility user agent.
- Like Pi, subscription requests begin with the system block
  `You are Claude Code, Anthropic's official CLI for Claude.` Helper's own
  task instructions and decision schema follow in a separate block. API-key
  requests receive none of those OAuth-specific additions.

Helper still sends text/screenshots through its existing decision/validation
loop. No provider computer-input tools are enabled or executed by this change.

## Storage and selection

Tokens live in the system credential manager under `Helper` / `anthropic-oauth`,
separate from Grok and ChatGPT. They refresh five minutes before expiry, with
refresh-token rotation supported. Tokens and pasted codes are never written to
`.env` or logs. The authorization input is cleared after submission/completion.
Revoked grants require a fresh sign-in; temporary refresh failures preserve them.

With `HELPER_PROVIDER=anthropic`, `HELPER_ANTHROPIC_AUTH_MODE` controls routing:

| Mode | Connection |
|---|---|
| `auto` (default) | Preserve an existing Anthropic API key; otherwise subscription OAuth. |
| `oauth` | Only the saved Claude subscription grant, even if an API key exists. |
| `api_key` | Only `HELPER_ANTHROPIC_API_KEY`, using API billing. |

The Account card writes the explicit mode when you select either connection.
There is no silent fallback to billed API access, Grok, or ChatGPT.
A 401 triggers one refresh retry. A 403/quota error does not remove the grant.
**Sign out of Claude** removes only Helper's local Claude grant; it does not
revoke the grant at Anthropic or remove API keys/other provider sign-ins.

## Verification

`tests/test_oauth_anthropic.py` covers PKCE, callback state checks, port fallback,
manual code formats, cancellation, timeout, refresh, keyring isolation, and
sanitized failures. `tests/test_anthropic_subscription.py` covers account UI,
explicit auth-mode selection, request headers, system blocks, and the engine's
actual text/image/schema serialization. Tests mock provider HTTP and credentials;
loopback callback tests use only a local test listener. They do not approve a
real account or consume inference tokens. Live subscription access still needs
your browser approval and a successful provider response.
