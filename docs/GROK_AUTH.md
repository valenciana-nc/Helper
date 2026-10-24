# Native Grok sign-in

Helper implements xAI's OAuth device authorization flow directly in Python.
It does not launch Pi, read Pi's authentication files, or reuse your browser cookies.

## Connect

1. Open **Account → Grok → Sign in with Grok**.
2. Approve the displayed code on the official xAI page opened in your browser.
   If the browser does not open, copy the address and code from the card.
3. Choose a Grok model, then click **Use subscription and restart**.

An eligible SuperGrok / X Premium account is required for subscription access.
xAI determines eligibility, available models, and limits; authenticating alone
is not proof of model access. **Sign out of Grok** removes Helper's local Grok
tokens, not your ChatGPT tokens or xAI API key. It does not revoke the grant on
xAI's server; manage account authorizations with xAI if needed.

## Protocol

The reference is Pi's [xAI OAuth implementation](https://github.com/earendil-works/pi/blob/136b87e/packages/ai/src/auth/oauth/xai.ts)
and [xAI provider](https://github.com/earendil-works/pi/blob/136b87e/packages/ai/src/providers/xai.ts).
The public client ID and scopes match that integration; they are not a client
secret. Helper identifies its device request with `referrer=helper`.
Third-party client availability and subscription policies can change upstream.

- Device authorization: `https://auth.x.ai/oauth2/device/code`.
- Tokens and refresh: `https://auth.x.ai/oauth2/token`.
- Scopes: `openid profile email offline_access grok-cli:access api:access`.
- The user code is shown locally; only approved tokens are saved.
- Polling respects the initial interval, pending replies, slow-down replies,
  expiry, and cancellation. Only official HTTPS xAI verification URLs are opened.
- Tokens are stored under `Helper` / `xai-oauth` in the OS credential manager
  (Windows Credential Manager on Windows), separate from ChatGPT authentication.
- Tokens refresh five minutes before expiry. Refresh-token rotation and omitted
  replacement refresh tokens are supported. Revoked grants require a new login.
- Closing the dashboard or quitting Helper cancels pending Grok login.
  An in-flight HTTP call may finish before cancellation is observed.

## Provider routing

`HELPER_PROVIDER=xai` uses `HELPER_XAI_AUTH_MODE`:

| Mode | Behavior |
|---|---|
| `auto` (default) | Preserve an existing `HELPER_XAI_API_KEY`; otherwise use Grok OAuth. |
| `oauth` | Use only the saved subscription grant, even if an API key exists. |
| `api_key` | Use only `HELPER_XAI_API_KEY` with separate API billing. |

The Account card writes the explicit mode when you choose either connection.
No OAuth tokens are written to `.env`. Choosing a subscription preserves any
saved API key but does not use it as a silent fallback. A missing Grok connection
never falls back to ChatGPT.

Subscription chat and screenshot-based engine requests go to
`https://api.x.ai/v1/responses` with bearer authentication and `store=false`.
Text, screenshots, and the existing decision schema still use Helper's local
validation/execution loop; xAI receives no native computer-input tools.
A 401 triggers one token-refresh retry; quota/403 errors do not erase the grant.
The existing API-key path remains at xAI's chat-completions endpoint.

## Validation

`tests/test_oauth_xai.py`, `tests/test_xai_subscription.py`, and
`tests/test_provider_accounts.py` use fake HTTP and isolated credentials. They
cover protocol handling, refresh, cancellation, account selection, and the real
engine's text/image/schema serialization. They do not prove that a particular
subscription can access a particular model. A live login requires your browser
approval; tests never approve an account or consume paid inference tokens.
