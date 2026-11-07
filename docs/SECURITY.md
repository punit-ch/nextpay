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

1. **No user accounts.** Per-plan capability tokens protect plan operations, but they are bearer credentials: anyone who obtains a token can access that plan. Keep tokens out of logs, URLs and support screenshots.
2. **Rate-limit storage is process-local.** Current limits are 10 AI recommendations/IP/minute and 20 plan creations/IP/minute. They reset on process restart and are not shared across multiple workers or instances. Use Redis or another shared store before scaling horizontally.
3. **Persistent production database is not configured.** The Render blueprint currently uses SQLite on an ephemeral filesystem. Data can be lost on redeploy/restart. Configure managed PostgreSQL and set `DATABASE_URL` before handling real users or relying on transaction history.
4. **PayPal Sandbox end-to-end validation remains outstanding.** Automated tests use mocks. Follow the real Sandbox checklist in `docs/SUBMISSION.md` before presenting the payment flow as verified against PayPal.
5. **Prompt injection.** Shopping text can influence the model's extracted category or budget, although server-side catalogue prices, policy validation and explicit user approval remain authoritative. Continue adversarial testing.
6. **Local database cleanup.** Back up the local `nextpay.db` before reconciling or removing old test rows. Never delete rows in `capturing` or `review_required` without checking the corresponding PayPal Sandbox order.
7. **Credential exposure.** Rotate PayPal Sandbox and OpenRouter credentials if `.env` was ever shared or committed. Keep all secrets in deployment environment variables, never frontend variables or Git.
8. **No webhook/background reconciliation.** Recovery currently depends on the buyer returning to the application or manually checking payment status. Add verified PayPal webhooks and a background reconciler before production use.

## Verified implementation notes

- Per-plan bearer credentials are generated with a cryptographically secure random generator; only SHA-256 hashes are stored.
- Plan read, approval, order creation, reconciliation and payment-ledger endpoints require the matching `X-Plan-Access-Token`.
- The PayPal browser return remains public by design; it identifies the server-side order and the server verifies order/capture data with PayPal before changing payment state.
- The PayPal cancel callback checks the returned order token against the order recorded for that plan.
- Rate limits are currently process-local: 10 AI recommendations and 20 plan creations per client IP per 60-second window.
