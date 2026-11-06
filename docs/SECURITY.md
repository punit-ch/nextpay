# NEXTPAY security review

Scope: practical review of this repository (Sandbox demo). **Evidence column:** `test` = covered by an automated test that was executed; `test*` = test written but not executed in the authoring environment (needs FastAPI, run `python -m pytest`); `read` = verified by reading code only; `you` = needs your action.

| # | Area | Finding / control | Evidence |
|---|---|---|---|
| 1 | Prices & totals | Never taken from the client. Plan creation, approval and order creation each recompute from the server catalogue. A tampered stored total does not change the amount sent to PayPal. | `test` (service), `test*` (API) |
| 2 | Budget & quantity | Pydantic bounds (`1..5`, budget `0..1000`) plus `policy.py` re-check at plan, approval and order time; capture refuses a ledger whose total exceeds the plan budget. | `test` |
| 3 | Approval | `create-order` returns `403` unless the plan is `approved_by_user`; approval is a conditional UPDATE so it cannot be granted twice. | `test`, `test*` |
| 4 | Order/capture ownership | Ledger row, plan, order ID and plan ID must agree before any PayPal call; PayPal `reference_id`/`custom_id` must equal the plan ID; capture IDs are unique per plan/payment. | `test` |
| 5 | Payment verification | Order ID, unit count, reference/custom ID, capture status/ID, currency, exact amount; mismatches → `review_required`. Approved orders are checked *before* capture. | `test` |
| 6 | Double charge | Deterministic `PayPal-Request-Id` for create (per plan) and capture (per order); conditional state claims; concurrent callbacks captured exactly once. | `test` |
| 7 | Unknown outcomes | Timeouts, 5xx, 429, auth and malformed replies never mark a payment failed or paid; they stay `capturing` and are reconciled. | `test` |
| 8 | Sandbox enforcement | `PAYPAL_BASE_URL` must equal the Sandbox host or all PayPal calls raise before any request is sent. | `test` |
| 9 | Secrets | `.env` is git-ignored and was never committed; secret values do not appear in any tracked file or git history (checked by value, not printed). No secrets in frontend code (`VITE_*` holds only the API URL). | `read` + scripted scan |
| 10 | Logging | Logs carry plan/order IDs, statuses and reason codes only. Credentials, tokens and PayPal payloads are never logged; PayPal error text is reduced to whitelisted codes. | `test` |
| 11 | Redirects | Browser redirects go only to fixed paths on `FRONTEND_URL`; query values are URL-encoded; PayPal's `approval_url` must be `https://*.paypal.com` (server and client check). | `test` (URL check), `read` |
| 12 | Path injection | PayPal order IDs are validated (`[A-Za-z0-9_-]{1,128}`) before being placed in a URL path. | `test` |
| 13 | CORS | Only `CORS_ORIGINS` / `FRONTEND_URL`; methods `GET, POST, OPTIONS`; header `Content-Type`; no credentials. | `test*` |
| 14 | Response headers | `X-Content-Type-Options: nosniff`; `Cache-Control: no-store` on `/api/*`. | `test*` |
| 15 | Cancel endpoint | PayPal appends `token`; when present it must match the plan's order. A cancel can never override `capturing`/`completed`. | `test` |
| 16 | Database consistency | Plan and ledger change in one transaction; SQLite foreign keys enabled; busy timeout set; audit rows in `payment_events`. | `test` |
| 17 | Test isolation | Tests use a temp DB and block non-loopback sockets; they can no longer write to `nextpay.db` or reach PayPal. | `test` |
| 18 | Debug endpoint | `/api/paypal/test-auth` returns `404` when `APP_ENV=production`. | `read` |

## Open risks (not fixed)

1. **`nextpay.db` is tracked by Git.** It holds purchase history. Run `git rm --cached nextpay.db` (keeps the file) and commit; `*.db` is now in `.gitignore`. You: **you**.
2. **No authentication.** Anyone with a `plan_id` can view it, cancel an unpaid checkout, or trigger `reconcile` (which only completes a purchase the buyer already approved at PayPal). IDs are random UUIDs, but this is not user-level authorisation.
3. **No rate limiting.** `/api/agent/recommend` can burn the free OpenRouter quota; `/api/plans` can fill the DB.
4. **Return URL parameters are untrusted by design.** `token` is only used to look up *our own* ledger row; all money decisions come from PayPal's API response.
5. **Prompt injection** in the shopping message cannot change prices or approval (the model only fills a validated intent), but it can influence the category/budget it extracts; the user still reviews everything.
6. **Your local database** contains rows from earlier test runs that wrote to it (e.g. several `review_required`, two `capturing`). Back it up before use; the two `capturing` rows will be reconciled with PayPal the next time their return URL or "check status" is used.
7. **Rotate credentials** if `.env` was ever shared (for example in a zip you sent to anyone). Sandbox keys are low-risk, but the OpenRouter key is a real account key.
