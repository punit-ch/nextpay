import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  LoaderCircle,
  RefreshCw,
  ShieldAlert,
  XCircle,
} from "lucide-react";

import { apiRequest, planAccessHeaders } from "./api";
import { Wordmark } from "./components/chrome.jsx";
import { SlipEdge } from "./components/Slip.jsx";
import {
  canShowReceipt,
  createLatestGate,
  pollingStatus,
  resolveView,
  shouldPoll,
} from "./paymentState";

const POLL_INTERVAL_MS = 4000;
const MAX_POLLS = 15;

const REASON_TEXT = {
  review_required:
    "The amount or details PayPal returned did not match your order.",
  status_mismatch: "NEXTPAY's records for this payment disagree.",
  ownership_mismatch: "NEXTPAY's records for this payment disagree.",
  declined: "PayPal reported that the payment method was declined.",
  order_voided: "The PayPal order expired or was voided.",
  order_not_approved: "The PayPal checkout was not completed.",
  plan_not_found: "NEXTPAY could not find this order.",
  payment_record_not_found: "NEXTPAY could not find this payment.",
  invalid_token: "The payment link was not valid.",
  invalid_payment_status: "This payment cannot be processed in its current state.",
  paypal_unavailable: "PayPal could not be reached to confirm the payment.",
  paypal_configuration: "NEXTPAY could not authenticate with PayPal Sandbox.",
  malformed_response: "PayPal returned an unexpected response.",
  capture_pending: "PayPal has accepted the payment but has not settled it yet.",
  capture_in_progress: "NEXTPAY is confirming this payment with PayPal.",
  cancel_conflict:
    "Your cancellation arrived, but PayPal shows this order as approved, so it was not cancelled.",
  internal_error: "NEXTPAY hit an unexpected error while confirming.",
};

const TONES = {
  emerald: "bg-ok-ink/10 text-ok-ink",
  amber: "bg-warn-ink/10 text-warn-ink",
  red: "bg-bad-ink/10 text-bad-ink",
  slate: "bg-ink/10 text-ink",
};

// Every result is a slip on the dark desk: the same object the buyer filled in.
function Shell({ tone, icon: Icon, title, children, actions }) {
  return (
    <div className="min-h-screen bg-canvas text-fg">
      <header className="border-b border-line">
        <div className="mx-auto flex max-w-[1180px] items-center px-5 py-4 sm:px-8">
          <Wordmark />
        </div>
      </header>

      <main className="mx-auto flex max-w-[1180px] justify-center px-5 py-12 sm:px-8 sm:py-16">
        <div className="on-paper w-full max-w-[460px] motion-safe:animate-slip-in">
          <section
            aria-labelledby="result-title"
            className="rounded-t-md bg-paper px-7 pb-8 pt-8 text-ink"
          >
            <div
              className={`flex size-12 items-center justify-center rounded-full ${TONES[tone] || TONES.slate}`}
            >
              <Icon size={26} aria-hidden="true" />
            </div>
            <h1
              id="result-title"
              className="mt-5 text-balance font-serif text-3xl font-bold leading-tight"
            >
              {title}
            </h1>
            <div className="mt-3 text-[15px] leading-7 text-ink-muted">
              {children}
            </div>
            <div className="mt-7 space-y-3 border-t border-dashed border-paper-line pt-6">
              {actions}
            </div>
          </section>
          <SlipEdge />
        </div>
      </main>
    </div>
  );
}

function PrimaryLink({ children }) {
  return (
    <a
      href="/"
      className="block rounded-md bg-indigo px-5 py-3.5 text-center font-bold text-white hover:bg-indigo-deep"
    >
      {children}
    </a>
  );
}

function SecondaryLink({ children }) {
  return (
    <a
      href="/"
      className="block rounded-md border border-paper-field-line px-5 py-3.5 text-center font-bold text-ink hover:bg-paper-deep"
    >
      {children}
    </a>
  );
}

function CheckButton({ checking, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={checking}
      className="flex w-full items-center justify-center gap-2 rounded-md bg-indigo px-5 py-3.5 font-bold text-white hover:bg-indigo-deep disabled:cursor-not-allowed disabled:opacity-60"
    >
      <RefreshCw size={18} className={checking ? "animate-spin" : ""} />
      {checking ? "Checking with PayPal..." : "Check payment status"}
    </button>
  );
}

function Note({ tone = "muted", children }) {
  const color = {
    muted: "text-ink-muted",
    warn: "text-warn-ink",
    bad: "text-bad-ink",
  }[tone];

  return <p className={`mt-3 text-sm leading-6 ${color}`}>{children}</p>;
}

// Only rendered for a payment the BACKEND has verified as completed.
function Receipt({ plan }) {
  if (!canShowReceipt(plan)) return null;

  const purchase = plan.purchase || {};

  return (
    <dl className="mt-6 space-y-3 border-t border-dashed border-paper-line pt-5 text-sm">
      <div className="flex justify-between gap-4">
        <dt className="text-ink-muted">Product</dt>
        <dd className="text-right font-bold text-ink">
          {purchase.product_name || "NEXTPAY purchase"}
        </dd>
      </div>
      <div className="flex justify-between gap-4">
        <dt className="text-ink-muted">Quantity</dt>
        <dd className="font-serif tabular-nums text-ink">
          {purchase.quantity ?? "—"}
        </dd>
      </div>
      <div className="flex items-end justify-between gap-4 border-t border-dashed border-paper-line pt-4">
        <dt className="text-ink-muted">Amount paid</dt>
        <dd className="font-serif text-3xl font-bold tabular-nums text-ink">
          ${purchase.total ?? "—"}
          <span className="ml-1.5 text-sm font-normal text-ink-muted">USD</span>
        </dd>
      </div>
      <div className="border-t border-dashed border-paper-line pt-4">
        <dt className="text-ink-muted">PayPal capture ID</dt>
        <dd className="mt-1 break-all font-mono text-xs text-ink">
          {plan.paypal_capture_id}
        </dd>
      </div>
      <div className="flex justify-between gap-4">
        <dt className="text-ink-muted">Payment status</dt>
        <dd className="font-bold uppercase text-ok-ink">
          {plan.payment_status}
        </dd>
      </div>
    </dl>
  );
}

/**
 * Presentational only: shows exactly what `view` says. The decision itself
 * is made by resolveView() from the backend's latest response.
 */
export function PaymentView({
  view,
  kind,
  plan,
  planId,
  reason,
  loadError,
  checking,
  checkMessage,
  pollingStopped = false,
  accessDenied = false,
  onCheckAgain,
}) {
  const reasonText = reason ? REASON_TEXT[reason] || null : null;

  if (view === "success") {
    return (
      <Shell
        tone="emerald"
        icon={CheckCircle2}
        title="Payment successful"
        actions={<PrimaryLink>Continue shopping</PrimaryLink>}
      >
        <p>Your PayPal Sandbox payment was captured and verified.</p>
        <Receipt plan={plan} />
      </Shell>
    );
  }

  if (view === "pending") {
    const cancelRaced = kind === "cancelled" || reason === "cancel_conflict";

    return (
      <Shell
        tone="amber"
        icon={Clock}
        title="Confirming your payment"
        actions={
          <>
            {planId && <CheckButton checking={checking} onClick={onCheckAgain} />}
            <SecondaryLink>Return to NEXTPAY</SecondaryLink>
          </>
        }
      >
        <p>
          NEXTPAY has not finished confirming this payment with PayPal. This is
          not a failure. Please do not pay again.
        </p>
        {cancelRaced && (
          <Note tone="warn">
            Your cancellation arrived while PayPal was already processing this
            payment, so it could not be applied. NEXTPAY will show the final
            result here.
          </Note>
        )}
        {!cancelRaced && reasonText && reason !== "capture_in_progress" && (
          <Note>{reasonText}</Note>
        )}
        {pollingStopped && (
          <div
            role="status"
            className="mt-4 rounded-md border border-warn-ink/30 bg-warn-ink/5 p-4 text-sm leading-6 text-ink"
          >
            <p className="font-bold">Automatic checking has stopped.</p>
            <p className="mt-1 text-ink-muted">
              Your payment has not been cancelled and has not been marked as
              failed. Press <strong>Check payment status</strong> to ask PayPal
              again. If it still says confirming, wait a minute and try once
              more. Please do not pay again.
            </p>
            {planId && (
              <p className="mt-2 break-all font-mono text-xs text-ink-muted">
                Reference: {planId}
              </p>
            )}
          </div>
        )}
        {loadError && <Note tone="bad">{loadError}</Note>}
        {checkMessage && <Note tone="warn">{checkMessage}</Note>}
      </Shell>
    );
  }

  if (view === "review") {
    return (
      <Shell
        tone="amber"
        icon={ShieldAlert}
        title="Payment under review"
        actions={
          <>
            {planId && !accessDenied && (
              <CheckButton checking={checking} onClick={onCheckAgain} />
            )}
            <SecondaryLink>Return to NEXTPAY</SecondaryLink>
          </>
        }
      >
        <p>
          NEXTPAY could not verify this payment against your order, so it has
          not been marked as paid and needs manual review.
        </p>
        {reasonText && <Note>{reasonText}</Note>}
        {planId && (
          <p className="mt-3 break-all font-mono text-xs text-ink-muted">
            Reference: {planId}
          </p>
        )}
      </Shell>
    );
  }

  if (view === "not_completed") {
    return (
      <Shell
        tone="red"
        icon={XCircle}
        title="Payment was not completed"
        actions={<PrimaryLink>Start a new purchase</PrimaryLink>}
      >
        <p>PayPal reported that this payment could not be completed.</p>
        {reasonText && <Note>{reasonText}</Note>}
      </Shell>
    );
  }

  if (view === "cancelled") {
    return (
      <Shell
        tone="slate"
        icon={AlertCircle}
        title="Checkout cancelled"
        actions={<PrimaryLink>Return to NEXTPAY</PrimaryLink>}
      >
        <p>
          You cancelled the PayPal checkout. No payment was taken and none is
          pending.
        </p>
      </Shell>
    );
  }

  if (view === "unverified") {
    return (
      <Shell
        tone="amber"
        icon={AlertCircle}
        title="Payment status unverified"
        actions={
          <>
            {planId && !accessDenied && (
              <CheckButton checking={checking} onClick={onCheckAgain} />
            )}
            <SecondaryLink>Return to NEXTPAY</SecondaryLink>
          </>
        }
      >
        <p>
          NEXTPAY could not confirm the state of this payment, so it is not
          showing a result. Check your PayPal activity before trying again.
        </p>
        {accessDenied && (
          <Note tone="warn">
            This purchase can only be viewed from the browser where it was
            started. Open NEXTPAY there, or check your PayPal activity.
          </Note>
        )}
        {loadError && <Note tone="bad">{loadError}</Note>}
        {checkMessage && <Note tone="warn">{checkMessage}</Note>}
      </Shell>
    );
  }

  // "incomplete": still awaiting approval at PayPal.
  return (
    <Shell
      tone="amber"
      icon={AlertCircle}
      title="Checkout not completed"
      actions={<PrimaryLink>Return to NEXTPAY</PrimaryLink>}
    >
      <p>
        This checkout was not completed and no payment has been confirmed. If
        you left PayPal without paying, nothing was charged.
      </p>
      {reasonText && <Note>{reasonText}</Note>}
    </Shell>
  );
}

export default function PaymentResult({ path }) {
  const params = new URLSearchParams(window.location.search);
  const planId = params.get("plan_id");
  const reason = params.get("reason");
  const kind = path.replace("/payment/", "");

  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(Boolean(planId));
  const [loadError, setLoadError] = useState("");
  const [loadStatus, setLoadStatus] = useState(null);
  const [checking, setChecking] = useState(false);
  const [checkMessage, setCheckMessage] = useState("");
  const [pollEpoch, setPollEpoch] = useState(0);
  const [pollsExhausted, setPollsExhausted] = useState(false);

  const gate = useMemo(() => createLatestGate(), []);
  const controller = useRef(null);
  const inFlight = useRef(false);
  const checkingRef = useRef(false);

  // Only the most recently issued request may update the screen.
  const loadPlan = useCallback(async () => {
    if (!planId) return;

    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    const ticket = gate.begin();
    inFlight.current = true;

    try {
      const data = await apiRequest(
        `/api/plans/${encodeURIComponent(planId)}`,
        { signal: current.signal, headers: planAccessHeaders(planId) },
      );
      if (!gate.isLatest(ticket)) return; // superseded: ignore
      setPlan(data);
      setLoadError("");
      setLoadStatus(null);
    } catch (err) {
      if (err?.name === "AbortError" || !gate.isLatest(ticket)) return;
      // Keep the last good state; just report the failure.
      setLoadError(err.message);
      setLoadStatus(err.status ?? null);
    } finally {
      if (gate.isLatest(ticket)) {
        inFlight.current = false;
        setLoading(false);
      }
    }
  }, [planId, gate]);

  useEffect(() => {
    loadPlan();
    return () => {
      gate.cancelAll();
      controller.current?.abort();
    };
  }, [loadPlan, gate]);

  const view = resolveView(kind, plan);

  // Poll (GET only; a result page never triggers a capture) while confirming.
  // The interval exists only while the screen is "pending", so it stops by
  // itself at any terminal state. A tick is skipped (not queued) when another
  // request or a manual check is still running, so requests never overlap.
  useEffect(() => {
    if (view !== "pending") return undefined;

    let attempts = 0;

    const timer = setInterval(() => {
      if (inFlight.current || checkingRef.current) return;

      if (!shouldPoll("pending", attempts, MAX_POLLS)) {
        // Budget spent: stop, and tell the buyer how to recover. This does
        // not touch the payment; only the manual check can ask PayPal again.
        clearInterval(timer);
        setPollsExhausted(true);
        return;
      }

      attempts += 1;
      loadPlan();
    }, POLL_INTERVAL_MS);

    return () => clearInterval(timer);
  }, [view, loadPlan, pollEpoch]);

  async function checkAgain() {
    if (checkingRef.current || !planId) return;
    checkingRef.current = true;
    setChecking(true);
    setCheckMessage("");
    setPollsExhausted(false); // a fresh polling budget starts after this check

    try {
      const result = await apiRequest(
        `/api/plans/${encodeURIComponent(planId)}/paypal/reconcile`,
        { method: "POST", headers: planAccessHeaders(planId) },
      );
      await loadPlan();
      if (result.outcome === "pending" || result.outcome === "processing") {
        setCheckMessage(
          "PayPal has not confirmed this payment yet. Your money is not lost; try again shortly.",
        );
      }
    } catch (err) {
      setCheckMessage(err.message);
    } finally {
      checkingRef.current = false;
      setChecking(false);
      setPollEpoch((epoch) => epoch + 1); // restart the polling budget
    }
  }

  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-canvas text-muted">
        <p role="status" className="flex items-center gap-3">
          <LoaderCircle className="animate-spin" size={20} />
          Checking your payment status...
        </p>
      </main>
    );
  }

  return (
    <PaymentView
      view={view}
      kind={kind}
      plan={plan}
      planId={planId}
      reason={reason}
      loadError={loadError}
      checking={checking}
      checkMessage={checkMessage}
      pollingStopped={pollingStatus(view, pollsExhausted ? MAX_POLLS : 0, MAX_POLLS) === "exhausted"}
      accessDenied={loadStatus === 403}
      onCheckAgain={checkAgain}
    />
  );
}
