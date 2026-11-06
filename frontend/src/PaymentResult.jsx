import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  LoaderCircle,
  RefreshCw,
  ShieldAlert,
  XCircle,
} from "lucide-react";

import { apiRequest } from "./api";

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
  capture_in_progress: "Another request is already confirming this payment.",
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

function resolveView(kind, plan) {
  const status = plan?.payment_status;

  if (status === "completed") return "success";
  if (status === "review_required") return "review";
  if (status === "failed") return "failed";
  if (status === "cancelled_by_buyer") return "cancelled";
  if (status === "capturing") return "pending";
  if (status === "awaiting_paypal_approval") {
    if (kind === "pending") return "pending";
    return kind === "cancelled" ? "cancelled" : "incomplete";
  }

  // Plan could not be loaded: fall back to what the URL says, cautiously.
  if (kind === "cancelled") return "cancelled";
  if (kind === "pending" || kind === "success") return "unverified";
  return "incomplete";
}

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

function Receipt({ plan }) {
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
          {plan.paypal_capture_id || "Unavailable"}
        </p>
      </div>
      <div className="flex justify-between">
        <span className="text-slate-500">Payment status</span>
        <span className="text-emerald-400">COMPLETED</span>
      </div>
    </div>
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
  const [checking, setChecking] = useState(false);
  const [checkMessage, setCheckMessage] = useState("");
  const polls = useRef(0);

  const loadPlan = useCallback(async () => {
    if (!planId) return null;
    try {
      const data = await apiRequest(
        `/api/plans/${encodeURIComponent(planId)}`,
      );
      setPlan(data.plan || data);
      setLoadError("");
      return data.plan || data;
    } catch (err) {
      setLoadError(err.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, [planId]);

  useEffect(() => {
    loadPlan();
  }, [loadPlan]);

  const view = resolveView(kind, plan);

  // While a payment is being confirmed, keep asking the backend for its state.
  useEffect(() => {
    if (view !== "pending" || !plan) return undefined;

    const timer = setInterval(() => {
      polls.current += 1;
      if (polls.current > MAX_POLLS) {
        clearInterval(timer);
        return;
      }
      loadPlan();
    }, POLL_INTERVAL_MS);

    return () => clearInterval(timer);
  }, [view, plan, loadPlan]);

  async function checkAgain() {
    if (checking || !planId) return;
    setChecking(true);
    setCheckMessage("");
    polls.current = 0;

    try {
      const result = await apiRequest(
        `/api/plans/${encodeURIComponent(planId)}/paypal/reconcile`,
        { method: "POST" },
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
      setChecking(false);
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

  const reasonText = reason && (REASON_TEXT[reason] || null);

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

  if (view === "pending" || view === "unverified") {
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
                onClick={checkAgain}
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
        {reasonText && <p className="mt-3 text-xs text-slate-500">{reasonText}</p>}
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
        actions={<PrimaryLink>Return to NEXTPAY</PrimaryLink>}
      >
        <p>
          NEXTPAY found a difference between your order and the payment
          details. The order was not marked as paid and needs manual review.
        </p>
        {planId && (
          <p className="mt-3 break-all text-xs text-slate-500">
            Reference: {planId}
          </p>
        )}
      </Shell>
    );
  }

  if (view === "failed") {
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
          You cancelled the PayPal checkout. No payment has been recorded by
          NEXTPAY.
        </p>
      </Shell>
    );
  }

  return (
    <Shell
      tone="amber"
      icon={AlertCircle}
      title="Payment not completed"
      actions={<PrimaryLink>Return to NEXTPAY</PrimaryLink>}
    >
      <p>
        NEXTPAY could not confirm a completed payment for this checkout. Check
        your PayPal activity before trying again.
      </p>
      {reasonText && <p className="mt-3 text-xs text-slate-500">{reasonText}</p>}
    </Shell>
  );
}
