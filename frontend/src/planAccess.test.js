import assert from "node:assert/strict";
import test from "node:test";

import { PLAN_ACCESS_HEADER, createPlanAccessStore } from "./planAccess.js";

const PLAN = "ec8a7304-05bf-4edb-be07-229d678de258";
const TOKEN = "q3J8vK2mXr9LpT5nWb7YcD4fGh1sZa6uEoI0xNyV_-k";

function memoryStorage() {
  const data = new Map();
  return {
    data,
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => void data.set(key, String(value)),
  };
}

test("header name matches the backend", () => {
  assert.equal(PLAN_ACCESS_HEADER, "X-Plan-Token");
});

test("a saved token is returned as plan access headers", () => {
  const store = createPlanAccessStore(memoryStorage());

  assert.equal(store.save(PLAN, TOKEN), true);
  assert.equal(store.get(PLAN), TOKEN);
  assert.deepEqual(store.headers(PLAN), { "X-Plan-Token": TOKEN });
});

test("the token survives the round trip to PayPal (a brand-new store, same storage)", () => {
  const storage = memoryStorage();
  createPlanAccessStore(storage).save(PLAN, TOKEN); // before leaving for PayPal

  const afterReturn = createPlanAccessStore(storage); // page reloaded on /payment/success
  assert.deepEqual(afterReturn.headers(PLAN), { "X-Plan-Token": TOKEN });
});

test("an unknown plan gets no headers (never someone else's token)", () => {
  const store = createPlanAccessStore(memoryStorage());
  store.save(PLAN, TOKEN);

  assert.deepEqual(store.headers("another-plan-id"), {});
  assert.equal(store.get("another-plan-id"), null);
});

test("tokens are kept per plan", () => {
  const store = createPlanAccessStore(memoryStorage());
  const other = "Zz9yX8wV7uT6sR5qP4oN3mL2kJ1iH0gF_e-dCbAaBb";

  store.save("plan-a", TOKEN);
  store.save("plan-b", other);

  assert.equal(store.get("plan-a"), TOKEN);
  assert.equal(store.get("plan-b"), other);
});

test("malformed plan ids and tokens are rejected and never stored or sent", () => {
  const storage = memoryStorage();
  const store = createPlanAccessStore(storage);

  const badPlans = ["", "a/b", "../x", "a b", "x".repeat(65), null, undefined, 42, {}, "__proto__", "constructor"];
  for (const bad of badPlans) {
    if (bad !== "constructor") assert.equal(store.save(bad, TOKEN), false, String(bad));
  }

  const badTokens = ["", "short", "has space in it 12345678901234567890", "x".repeat(129), null, undefined, 42, {}];
  for (const bad of badTokens) {
    assert.equal(store.save(PLAN, bad), false, String(bad));
  }

  assert.equal(storage.data.size, 0);
  assert.deepEqual(store.headers(PLAN), {});
});

test("prototype keys can never produce a token", () => {
  const store = createPlanAccessStore(memoryStorage());

  for (const key of ["constructor", "toString", "hasOwnProperty", "__proto__"]) {
    assert.equal(store.get(key), null, key);
    assert.deepEqual(store.headers(key), {}, key);
  }
});

test("works in memory when storage is unavailable", () => {
  const store = createPlanAccessStore(null);

  assert.equal(store.save(PLAN, TOKEN), true);
  assert.deepEqual(store.headers(PLAN), { "X-Plan-Token": TOKEN });
});

test("works for this page when writing to storage fails (quota / private mode)", () => {
  const storage = {
    getItem: () => null,
    setItem: () => {
      throw new Error("QuotaExceededError");
    },
  };
  const store = createPlanAccessStore(storage);

  assert.equal(store.save(PLAN, TOKEN), true);
  assert.deepEqual(store.headers(PLAN), { "X-Plan-Token": TOKEN });
});

test("a storage that throws on read does not break the app", () => {
  const storage = {
    getItem: () => {
      throw new Error("SecurityError");
    },
    setItem: () => {},
  };
  const store = createPlanAccessStore(storage);

  assert.deepEqual(store.headers(PLAN), {});
  assert.equal(store.save(PLAN, TOKEN), true);
  assert.deepEqual(store.headers(PLAN), { "X-Plan-Token": TOKEN });
});

test("corrupt storage is treated as empty and repaired by the next save", () => {
  for (const corrupt of ["{not json", "null", "[1,2]", '"text"', "42"]) {
    const storage = memoryStorage();
    storage.data.set("nextpay.planAccess.v1", corrupt);
    const store = createPlanAccessStore(storage);

    assert.deepEqual(store.headers(PLAN), {}, corrupt);
    assert.equal(store.save(PLAN, TOKEN), true, corrupt);
    assert.deepEqual(store.headers(PLAN), { "X-Plan-Token": TOKEN }, corrupt);
  }
});

test("tampered stored entries are ignored", () => {
  const storage = memoryStorage();
  storage.data.set(
    "nextpay.planAccess.v1",
    JSON.stringify({ [PLAN]: { token: "short" }, other: { token: 12345 }, third: "x" }),
  );
  const store = createPlanAccessStore(storage);

  assert.deepEqual(store.headers(PLAN), {});
  assert.deepEqual(store.headers("other"), {});
  assert.deepEqual(store.headers("third"), {});
});

test("only the newest 25 plans are kept", () => {
  const storage = memoryStorage();
  const store = createPlanAccessStore(storage);
  const realNow = Date.now;
  let clock = 1_000;
  Date.now = () => (clock += 1);

  try {
    for (let i = 0; i < 30; i += 1) {
      store.save(`plan-${i}`, `T${String(i).padStart(2, "0")}${"a".repeat(30)}`);
    }
  } finally {
    Date.now = realNow;
  }

  const kept = Object.keys(JSON.parse(storage.data.get("nextpay.planAccess.v1")));
  assert.equal(kept.length, 25);
  assert.equal(store.get("plan-29") !== null, true);
  assert.equal(store.get("plan-0"), null);
});

test("forget removes a plan's token", () => {
  const store = createPlanAccessStore(memoryStorage());
  store.save(PLAN, TOKEN);

  store.forget(PLAN);

  assert.deepEqual(store.headers(PLAN), {});
});
