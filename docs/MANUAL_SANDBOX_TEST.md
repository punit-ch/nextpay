# Manual PayPal Sandbox test

Run the backend (`python -m uvicorn app.main:app --reload`) and frontend (`npm run dev`) with your Sandbox credentials in `.env`. Keep the uvicorn window visible: each callback logs one line.

**Reading plan data by hand.** Plan endpoints now require the plan's access token, so opening `/api/plans/<id>` in the address bar returns `403`. Get the token from the browser: DevTools → Application → Local Storage → `http://localhost:5173` → key `nextpay.planAccess.v1` (a JSON object keyed by plan id). Then in PowerShell:

```powershell
$h = @{ "X-Plan-Token" = "<token>" }
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/plans/<plan_id>
Invoke-RestMethod -Headers $h http://127.0.0.1:8000/api/plans/<plan_id>/payment
```

The same calls without `-Headers` must fail with `403`: that is the access protection working.

## A. Successful payment

1. Open <http://localhost:5173>, enter a goal, pick a product, **Review purchase**, then **Approve purchase & continue to PayPal**.
2. On PayPal, log in with your Sandbox **buyer** account and click **Pay Now**.
3. Expected browser URL: `/payment/success?plan_id=...` (or briefly `/payment/pending`, then it switches by itself).
4. Expected screen: **Payment successful** with a capture ID.
5. Expected uvicorn log: `return callback plan=<id> outcome=success reason=captured`.
6. Verify (with the header, see above): `GET /api/plans/<plan_id>` shows `"payment_status": "completed"`, `"payment_verified": true`, a non-null `paypal_capture_id`.
7. Refresh the success page and the old return URL: still one capture (`GET /api/plans/<plan_id>/payment`, with the header, lists a single `capture_completed` event).

## B. Cancelled payment

1. Start a new purchase as above (a new plan).
2. On the PayPal page click **Cancel and return to NEXTPAY** (do not log in and pay).
3. Expected browser URL: `/payment/cancelled?plan_id=...`.
4. Expected screen: **Checkout cancelled**.
5. Expected log: `cancel callback plan=<id> outcome=cancelled reason=cancelled_by_buyer`.
6. Verify (with the header): `GET /api/plans/<plan_id>` shows `"payment_status": "cancelled_by_buyer"`, `"payment_verified": false`, `paypal_capture_id: null`.
7. Press the browser Back button to the PayPal page and approve, if it lets you: the return callback must show **Checkout cancelled** and the log must say `outcome=cancelled`; no capture occurs.

## C. If something looks wrong

Copy the uvicorn lines containing `return callback` / `cancel callback` for that `plan=` and the JSON from `GET /api/plans/<plan_id>/payment` (with the header) (it lists every state change with timestamps). Those show the exact order of events.

- `outcome=pending reason=capture_in_progress` on a **cancel**: PayPal had already approved the order; the screen should settle on the final result by itself.
- `outcome=pending reason=cancel_conflict`: PayPal reports the order approved, so the cancel was intentionally not applied.
- `reason=paypal_unavailable` / `paypal_configuration`: PayPal could not be reached or the Sandbox credentials are wrong; nothing was charged and the page offers **Check payment status**.

## D. Plan access protection

1. Create a plan in the UI, then in DevTools → Local Storage copy the token for that plan.
2. `GET /api/plans/<plan_id>` **without** the header → `403`; with a wrong token → `403`; with the right token → `200`.
3. Open the success/cancelled page URL in a **private window** (no stored token) → **Payment status unverified** with the message that the purchase can only be viewed from the browser where it was started. The payment itself is unaffected.
4. Complete a normal payment again: the success page shows the receipt because the token survived the trip to PayPal and back.
