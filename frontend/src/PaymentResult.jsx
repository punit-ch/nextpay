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
  emerald: {
    border: "border-emerald-500/20",
    icon: "bg-emerald-500/10 text-emerald-400",
  },
  amber: {
    border: "border-amber-500/20",
    icon: "bg-amber-500/10 text-amber-300",
  },
  red: {
    border: "border-red-500/20",
    icon: "bg-red-500/10 text-red-300",
  },
  slate: {
    border: "border-white/10",
    icon: "bg-white/5 text-slate-300",
  },
};

function Shell({ tone, icon: Icon, title, children, actions }) {
  const styles = TONES[tone] || TONES.slate;

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#09090f] px-5 py-12 text-white">
      <div
        className={`w-full max-w-lg rounded-3xl border ${styles.border} bg-[#11111b] p-8`}
      >
        <div
          className={`mx-auto flex h-16 w-16 items-center justify-center rounded-full ${styles.icon}`}
        >
          <Icon size={32} />
        </div>
        <h1 className="mt-6 text-center text-2xl font-bold">{title}</h1>
        <div className="mt-3 text-center text-sm leading-6 text-slate-400">
          {children}
        </div>
        <div className="mt-7 space-y-3">{actions}</div>
      </div>
    </main>
  );
}

function PrimaryLink({ children }) {
  return (
    <a
      href="/"
      className="block rounded-xl bg-violet-600 px-5 py-4 text-center font-semibold hover:bg-violet-500"
    >
      {children}
    </a>
  );
}

// Only rendered for a payment the BACKEND has verified as completed.
function Receipt({ plan }) {
  if (!canShowReceipt(plan)) return null;

  const purchase = plan.purchase || {};

  return (
    <div className="mt-8 space-y-4 rounded-2xl bg-[#09090f] p-5 text-left text-sm">
      <div className="flex justify-between gap-4">
        <span className="text-slate-500">Product</span>
        <span className="text-right text-white">
          {purchase.product_name || "NEXTPAY purchase"}
        </span>
      </div>
      <div className="flex justify-between">
        <span className="text-slate-500">Quantity</span>
        <span className="text-white">{purchase.quantity ?? "—"}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-slate-500">Amount</span>
        <span className="font-bold text-emerald-300">
          ${purchase.total ?? "—"} USD
        </span>
      </div>
      <div className="border-t border-white/10 pt-4">
        <p className="text-xs text-slate-500">PayPal capture ID</p>
        <p className="mt-1 break-all text-xs text-white">
          {plan.paypal_capture_id}
        </p>
      </div>
      <div className="flex justify-between">
        <span className="text-slate-500">Payment status</span>
        <span className="uppercase text-emerald-400">{plan.payment_status}</span>
      </div>
    </div>
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
            {planId && (
              <button
                type="button"
                onClick={onCheckAgain}
                disabled={checking}
                className="flex w-full items-center justify-center gap-2 rounded-xl bg-violet-600 px-5 py-4 font-semibold hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <RefreshCw
                  size={18}
                  className={checking ? "animate-spin" : ""}
                />
                {checking ? "Checking with PayPal..." : "Check payment status"}
              </button>
            )}
            <PrimaryLink>Return to NEXTPAY</PrimaryLink>
          </>
        }
      >
        <p>
          NEXTPAY has not finished confirming this payment with PayPal. This is
          not a failure. Please do not pay again.
        </p>
        {cancelRaced && (
          <p className="mt-3 text-xs text-amber-200">
            Your cancellation arrived while PayPal was already processing this
            payment, so it could not be applied. NEXTPAY will show the final
            result here.
          </p>
        )}
        {!cancelRaced && reasonText && (
          <p className="mt-3 text-xs text-slate-500">{reasonText}</p>
        )}
        {pollingStopped && (
          <div
            role="status"
            className="mt-4 rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 text-left text-xs leading-5 text-amber-100"
          >
            <p className="font-semibold">Automatic checking has stopped.</p>
            <p className="mt-1">
              Your payment has not been cancelled and has not been marked
              as failed. Press <strong>Check payment status</strong> to ask
              PayPal again. If it still says confirming, wait a minute and
              try once more. Please do not pay again.
            </p>
            {planId && (
              <p className="mt-2 break-all text-amber-200/70">
                Reference: {planId}
              </p>
            )}
          </div>
        )}
        {loadError && <p className="mt-3 text-xs text-red-300">{loadError}</p>}
        {checkMessage && (
          <p className="mt-3 text-xs text-amber-200">{checkMessage}</p>
        )}
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
      <button
        type="button"
        onClick={onCheckAgain}
        disabled={checking}
        className="flex w-full items-center justify-center gap-2 rounded-xl bg-violet-600 px-5 py-4 font-semibold hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <RefreshCw
          size={18}
          className={checking ? "animate-spin" : ""}
        />
        {checking ? "Checking with PayPal..." : "Check payment status"}
      </button>
    )}
    <PrimaryLink>Return to NEXTPAY</PrimaryLink>
  </>
}
      >
        <p>
          NEXTPAY could not verify this payment against your order, so it has
          not been marked as paid and needs manual review.
        </p>
        {reasonText && <p className="mt-3 text-xs text-slate-500">{reasonText}</p>}
        {planId && (
          <p className="mt-3 break-all text-xs text-slate-500">
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
        {reasonText && <p className="mt-3 text-xs text-slate-500">{reasonText}</p>}
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
              <button
                type="button"
                onClick={onCheckAgain}
                disabled={checking}
                className="flex w-full items-center justify-center gap-2 rounded-xl bg-violet-600 px-5 py-4 font-semibold hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <RefreshCw size={18} className={checking ? "animate-spin" : ""} />
                {checking ? "Checking with PayPal..." : "Check payment status"}
              </button>
            )}
            <PrimaryLink>Return to NEXTPAY</PrimaryLink>
          </>
        }
      >
        <p>
          NEXTPAY could not confirm the state of this payment, so it is not
          showing a result. Check your PayPal activity before trying again.
        </p>
        {accessDenied && (
          <p className="mt-3 text-xs text-amber-200">
            This purchase can only be viewed from the browser where it was
            started. Open NEXTPAY there, or check your PayPal activity.
          </p>
        )}
        {loadError && <p className="mt-3 text-xs text-red-300">{loadError}</p>}
        {checkMessage && (
          <p className="mt-3 text-xs text-amber-200">{checkMessage}</p>
        )}
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
      {reasonText && <p className="mt-3 text-xs text-slate-500">{reasonText}</p>}
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
      <main className="flex min-h-screen items-center justify-center bg-[#09090f] text-slate-400">
        <p className="flex items-center gap-3">
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
