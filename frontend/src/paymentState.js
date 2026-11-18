// Pure payment-state logic for the result pages. No React, no network:
// everything here is unit-tested with `npm test`.
//
// The backend is the single source of truth. The frontend never decides on
// its own that a payment succeeded: it only reflects the latest response.

// Must match app/payments.py exactly. tests/test_frontend_contract.py fails
// if the two ever drift apart.
export const PAYMENT_STATUS = Object.freeze({
  NOT_STARTED: "not_started",
  AWAITING: "awaiting_paypal_approval",
  CAPTURING: "capturing",
  COMPLETED: "completed",
  CANCELLED: "cancelled_by_buyer",
  REVIEW: "review_required",
  FAILED: "failed",
});

const TERMINAL = new Set([
  PAYMENT_STATUS.COMPLETED,
  PAYMENT_STATUS.CANCELLED,
  PAYMENT_STATUS.REVIEW,
  PAYMENT_STATUS.FAILED,
]);

export function isTerminalStatus(status) {
  return TERMINAL.has(status);
}

/**
 * True only when the backend itself confirms a verified, completed payment.
 * `payment_verified` is computed server-side from the plan AND the ledger
 * (matching order and capture IDs, amount, currency).
 */
export function isVerifiedCompleted(plan) {
  return Boolean(
    plan &&
      plan.payment_status === PAYMENT_STATUS.COMPLETED &&
      plan.payment_verified === true &&
      plan.status === "approved_by_user" &&
      typeof plan.paypal_capture_id === "string" &&
      plan.paypal_capture_id.length > 0,
  );
}

/** A receipt may only be rendered for a verified completed payment. */
export function canShowReceipt(plan) {
  return isVerifiedCompleted(plan);
}

/**
 * Which screen to show.
 *
 * kind: the URL segment (success | pending | failure | cancelled)
 * plan: the latest backend response, or null when it could not be loaded.
 *
 * The URL only ever picks between "pending" and "incomplete" while the plan
 * is still awaiting approval; it never overrides what the backend says, and
 * "success" is impossible without a verified completed payment.
 */
export function resolveView(kind, plan) {
  if (!plan) return "unverified";

  switch (plan.payment_status) {
    case PAYMENT_STATUS.COMPLETED:
      // Completed but not verified (missing/mismatched capture): never a receipt.
      return isVerifiedCompleted(plan) ? "success" : "review";
    case PAYMENT_STATUS.CAPTURING:
      return "pending";
    case PAYMENT_STATUS.AWAITING:
      return kind === "pending" ? "pending" : "incomplete";
    case PAYMENT_STATUS.CANCELLED:
      return "cancelled";
    case PAYMENT_STATUS.FAILED:
      return "not_completed";
    case PAYMENT_STATUS.REVIEW:
      return "review";
    default:
      return "unverified";
  }
}

/**
 * "polling"   automatic GET checks are still running
 * "exhausted" the budget is spent; the buyer must use the manual check
 * "idle"      nothing to confirm (any non-pending screen)
 *
 * Exhausting the budget changes nothing about the payment: it only decides
 * what guidance the screen shows. It never implies success or cancellation.
 */
export function pollingStatus(view, attempts, maxPolls) {
  if (view !== "pending") return "idle";
  return attempts < maxPolls ? "polling" : "exhausted";
}

/** Poll only while a payment is being confirmed. */
export function shouldPoll(view, pollCount, maxPolls) {
  return view === "pending" && pollCount < maxPolls;
}

/**
 * Request sequencing. Every request gets a ticket; only the response of the
 * most recently issued request may be applied. An older response that arrives
 * late (for example an earlier "completed" after a newer "capturing") is
 * discarded instead of overwriting what the user sees.
 */
export function createLatestGate() {
  let issued = 0;

  return {
    begin() {
      issued += 1;
      return issued;
    },
    isLatest(ticket) {
      return ticket === issued;
    },
    /** Invalidate everything in flight (e.g. on unmount). */
    cancelAll() {
      issued += 1;
    },
  };
}
