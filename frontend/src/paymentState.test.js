import assert from "node:assert/strict";
import test from "node:test";

import {
  PAYMENT_STATUS,
  canShowReceipt,
  createLatestGate,
  isTerminalStatus,
  isVerifiedCompleted,
  pollingStatus,
  resolveView,
  shouldPoll,
} from "./paymentState.js";

const completed = {
  status: "approved_by_user",
  payment_status: "completed",
  payment_verified: true,
  paypal_capture_id: "CAPTURE-123",
};

const plan = (payment_status, extra = {}) => ({
  status: "approved_by_user",
  payment_status,
  payment_verified: false,
  paypal_capture_id: null,
  ...extra,
});

// Scenario 15 ---------------------------------------------------------------
test("15: a `capturing` response never shows success", () => {
  for (const kind of ["success", "pending", "failure", "cancelled"]) {
    const view = resolveView(kind, plan("capturing"));
    assert.equal(view, "pending", `kind=${kind}`);
    assert.notEqual(view, "success");
  }
});

test("15b: even the /payment/success URL cannot force a success screen", () => {
  assert.equal(resolveView("success", plan("capturing")), "pending");
  assert.equal(resolveView("success", plan("awaiting_paypal_approval")), "incomplete");
  assert.equal(resolveView("success", null), "unverified");
});

// Scenario 16 ---------------------------------------------------------------
test("16: an older completed response arriving after a newer pending one is discarded", () => {
  const gate = createLatestGate();
  let shown = null;

  const older = gate.begin(); // request 1: will resolve LATE with "completed"
  const newer = gate.begin(); // request 2: resolves first with "capturing"

  // The newer response arrives first and is applied.
  if (gate.isLatest(newer)) shown = plan("capturing");
  // The older response arrives afterwards and must be ignored.
  if (gate.isLatest(older)) shown = completed;

  assert.equal(shown.payment_status, "capturing");
  assert.equal(resolveView("pending", shown), "pending");
});

test("16b: a superseded response is ignored even if it arrives first", () => {
  const gate = createLatestGate();
  const first = gate.begin();
  const second = gate.begin();

  assert.equal(gate.isLatest(first), false);
  assert.equal(gate.isLatest(second), true);
});

test("16c: cancelAll (unmount) invalidates in-flight requests", () => {
  const gate = createLatestGate();
  const ticket = gate.begin();
  gate.cancelAll();
  assert.equal(gate.isLatest(ticket), false);
});

// Scenario 17 ---------------------------------------------------------------
test("17: a cancelled status shows the cancellation screen", () => {
  assert.equal(resolveView("cancelled", plan(PAYMENT_STATUS.CANCELLED)), "cancelled");
  // ...regardless of which URL the buyer happened to land on.
  assert.equal(resolveView("pending", plan("cancelled_by_buyer")), "cancelled");
  assert.equal(resolveView("failure", plan("cancelled_by_buyer")), "cancelled");
});

test("17b: the cancelled URL does not claim a cancellation the backend has not recorded", () => {
  // Still awaiting approval: show 'incomplete', not 'cancelled'.
  assert.equal(resolveView("cancelled", plan("awaiting_paypal_approval")), "incomplete");
  // Cancel lost a conflict with a capture in progress: confirm, do not cancel.
  assert.equal(resolveView("cancelled", plan("capturing")), "pending");
});

// Scenario 18 ---------------------------------------------------------------
test("18: a receipt is never shown for a non-completed payment", () => {
  const statuses = [
    "not_started",
    "awaiting_paypal_approval",
    "capturing",
    "cancelled_by_buyer",
    "failed",
    "review_required",
    "some_unknown_state",
    undefined,
    null,
    "",
  ];

  for (const status of statuses) {
    // Even if capture ID and the verified flag are (wrongly) present.
    const hostile = plan(status, { payment_verified: true, paypal_capture_id: "CAP-1" });
    assert.equal(canShowReceipt(hostile), false, `status=${status}`);
    assert.notEqual(resolveView("success", hostile), "success", `status=${status}`);
  }

  assert.equal(canShowReceipt(null), false);
  assert.equal(canShowReceipt(undefined), false);
});

test("18b: completed is not enough; the server must verify it and a capture ID must exist", () => {
  assert.equal(canShowReceipt(completed), true);
  assert.equal(resolveView("success", completed), "success");

  const cases = {
    "not verified by server": { ...completed, payment_verified: false },
    "verified flag missing": { ...completed, payment_verified: undefined },
    "no capture id": { ...completed, paypal_capture_id: null },
    "empty capture id": { ...completed, paypal_capture_id: "" },
    "numeric capture id": { ...completed, paypal_capture_id: 42 },
    "plan not approved": { ...completed, status: "awaiting_user_approval" },
    "truthy string flag": { ...completed, payment_verified: "true" },
  };

  for (const [name, candidate] of Object.entries(cases)) {
    assert.equal(canShowReceipt(candidate), false, name);
    assert.equal(resolveView("success", candidate), "review", name);
  }
});

// Table from the brief ----------------------------------------------------
test("status to screen mapping", () => {
  const expected = {
    completed: "success",
    capturing: "pending",
    awaiting_paypal_approval: "incomplete",
    cancelled_by_buyer: "cancelled",
    failed: "not_completed",
    review_required: "review",
    not_started: "unverified",
    mystery: "unverified",
  };

  for (const [status, view] of Object.entries(expected)) {
    const candidate = status === "completed" ? completed : plan(status);
    assert.equal(resolveView("failure", candidate), view, status);
  }

  assert.equal(resolveView("pending", plan("awaiting_paypal_approval")), "pending");
  assert.equal(resolveView("failure", null), "unverified");
});

// Polling -----------------------------------------------------------------
test("polling happens only while pending and stops at a terminal state or the limit", () => {
  assert.equal(shouldPoll("pending", 0, 15), true);
  assert.equal(shouldPoll("pending", 14, 15), true);
  assert.equal(shouldPoll("pending", 15, 15), false);

  for (const view of ["success", "cancelled", "not_completed", "review", "incomplete", "unverified"]) {
    assert.equal(shouldPoll(view, 0, 15), false, view);
  }
});

test("terminal statuses", () => {
  for (const status of ["completed", "cancelled_by_buyer", "failed", "review_required"]) {
    assert.equal(isTerminalStatus(status), true, status);
  }
  for (const status of ["not_started", "awaiting_paypal_approval", "capturing", "x"]) {
    assert.equal(isTerminalStatus(status), false, status);
  }
});

test("status values are frozen", () => {
  assert.throws(() => {
    "use strict";
    PAYMENT_STATUS.COMPLETED = "paid";
  });
  assert.equal(isVerifiedCompleted(completed), true);
});

// Recovery after polling stops ----------------------------------------------
test("polling status: polling, then exhausted, never for non-pending screens", () => {
  assert.equal(pollingStatus("pending", 0, 15), "polling");
  assert.equal(pollingStatus("pending", 14, 15), "polling");
  assert.equal(pollingStatus("pending", 15, 15), "exhausted");

  for (const view of ["success", "cancelled", "not_completed", "review", "incomplete", "unverified"]) {
    assert.equal(pollingStatus(view, 99, 15), "idle", view);
  }
});

test("an exhausted polling budget never changes which screen is shown", () => {
  // Still 'capturing' on the backend: still the 'confirming' screen, not
  // success and not cancelled, no matter how long we waited.
  for (let waited = 0; waited < 100; waited += 10) {
    assert.equal(resolveView("pending", plan("capturing")), "pending");
  }
});

test("only a fresh verified backend response can leave the pending screen", () => {
  assert.equal(resolveView("pending", plan("capturing")), "pending");
  assert.equal(resolveView("pending", completed), "success");
  assert.equal(resolveView("pending", plan("cancelled_by_buyer")), "cancelled");
  assert.equal(resolveView("pending", plan("failed")), "not_completed");
});
