# Hackathon submission kit

Nothing below claims results, users or metrics that do not exist.

## Short description (≈100 words)

NEXTPAY is an AI-assisted shopping agent that keeps the human in control of the money. Users describe what they want in plain English; an LLM (OpenRouter, free tier) converts it into a structured intent, and the server (not the model) finds matching catalogue items and enforces prices, quantity and budget. The user reviews a purchase plan and explicitly approves it before a PayPal Sandbox checkout is created. After checkout, NEXTPAY verifies PayPal's own order and capture data, records every state change in an audit ledger, and recovers safely from timeouts, duplicate callbacks and crashes without ever charging twice.

## One-line pitch

*Agentic shopping you can trust: the AI proposes, the server enforces, you approve, PayPal confirms.*

## Presentation outline (5-7 slides)

1. **Problem**: AI agents + payments = misread budgets, surprise charges, ambiguous failures.
2. **Idea**: separate AI interpretation from server-enforced decisions and explicit approval.
3. **Demo flow**: prompt → intent → plan → approve → PayPal Sandbox → verified receipt (live demo or recording).
4. **Architecture**: use the README architecture diagram.
5. **Payment safety**: state machine + sequence diagram; "unknown ≠ failed"; idempotency; audit ledger.
6. **Engineering evidence**: test counts (fill in from your own `pytest` run) and the security review in `docs/SECURITY.md`.
7. **Limitations & next steps**: webhooks, auth, background reconciler, Postgres (honest list from the README).

## Live-demo script (3 minutes)

1. Prompt: "I need comfortable headphones for studying, under $70." (show intent card)
2. Select headphones → Review purchase → point out the *server-validated* total.
3. Lower the budget to $50 → show the server rejection.
4. Approve → PayPal Sandbox → pay with the sandbox buyer → receipt with capture ID.
5. Open `/api/plans/<id>/payment` → ledger + audit events.
6. Second run: cancel on PayPal → "Checkout cancelled".
7. Refresh the return URL → still one capture.

## Screenshots and evidence to collect (you must produce these)

- [ ] Landing page (desktop and phone width)
- [ ] AI intent + recommendation after a real OpenRouter call
- [ ] Review purchase with server-validated total
- [ ] Budget-exceeded rejection
- [ ] PayPal Sandbox login/approval page
- [ ] Payment successful receipt (capture ID visible)
- [ ] Checkout cancelled page
- [ ] "Confirming your payment" page (e.g. by stopping the backend during capture)
- [ ] `GET /api/plans/<id>/payment` JSON (ledger + events)
- [ ] PayPal Sandbox dashboard → transaction for the same capture ID
- [ ] Terminal: `python -m pytest -q` full output
- [ ] Terminal: `npm run build` success
- [ ] 60-90 s screen recording of the full flow

## Still requires real PayPal Sandbox testing

These were verified only against mocks. Please run each once with real Sandbox credentials:

1. Order creation returns an `approve` link and the redirect round-trip lands on `/payment/success?plan_id=…`.
2. A real `GET /v2/checkout/orders/{id}` response for an `APPROVED` order includes `purchase_units[0].reference_id`, `custom_id` and `amount` (the code tolerates their absence before capture, but verifies them strictly afterwards).
3. A real capture response shape (`purchase_units[0].payments.captures[0]`).
4. Repeating the capture call with the same `PayPal-Request-Id` returns the original result (idempotency behaviour).
5. A cancelled checkout appends `token=<order id>` to the cancel URL.
6. Declined-instrument behaviour (use PayPal's sandbox negative-testing options) maps to `failed`.

## Deployment checklist (requires your accounts; nothing is done for you)

1. Push to GitHub **after** `git rm --cached nextpay.db` (see `docs/SECURITY.md`).
2. Backend: create a Render web service from `render.yaml`; set `BACKEND_URL`, `FRONTEND_URL`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `OPENROUTER_API_KEY` in the dashboard (never in Git).
3. Frontend: import `frontend/` into Vercel or Netlify; set `VITE_API_URL` to the backend URL; the SPA rewrite files are already present.
4. Set the backend's `FRONTEND_URL` to the deployed frontend URL and redeploy.
5. Re-run the demo on the deployed URLs.
6. Expect SQLite data to reset on redeploy on a free instance, and the first request after idling to be slow. Confirm each provider's current free-tier terms.
