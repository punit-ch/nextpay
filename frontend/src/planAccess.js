// Plan access tokens (pure logic; no React, no network, no import.meta).
//
// The backend returns a plan's access token exactly once, in the response to
// POST /api/plans. It must survive the trip to PayPal and back, so it is kept
// in localStorage (same origin before and after the redirect) and sent in the
// X-Plan-Token header on every request that acts on that plan.
//
// Storage can be unavailable (private mode, blocked, quota). Every storage
// access is guarded and an in-memory copy keeps the current page working.

export const PLAN_ACCESS_HEADER = "X-Plan-Token";

const STORAGE_KEY = "nextpay.planAccess.v1";
const MAX_PLANS = 25;

// Strict shapes: nothing else is ever stored or sent.
const PLAN_ID = /^[A-Za-z0-9-]{1,64}$/;
const TOKEN = /^[A-Za-z0-9_-]{20,128}$/;

function isPlanId(value) {
  return typeof value === "string" && PLAN_ID.test(value);
}

function isToken(value) {
  return typeof value === "string" && TOKEN.test(value);
}

/** The browser's localStorage, or null when it cannot be used. */
export function browserStorage() {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null; // accessing it can throw (SecurityError)
  }
}

export function createPlanAccessStore(storage = null) {
  let fallback = {};

  function load() {
    let stored = {};

    if (storage) {
      try {
        const raw = storage.getItem(STORAGE_KEY);
        const parsed = raw ? JSON.parse(raw) : {};
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          stored = parsed;
        }
      } catch {
        stored = {}; // unreadable or corrupt: treat as empty, the next save repairs it
      }
    }

    // Entries that could not be persisted stay usable for this page.
    return { ...fallback, ...stored };
  }

  function persist(entries) {
    fallback = entries;

    if (!storage) return;

    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(entries));
    } catch {
      // Quota or private mode: the in-memory copy still works for this page.
    }
  }

  function entryToken(entries, planId) {
    if (!isPlanId(planId) || !Object.hasOwn(entries, planId)) return null;
    const token = entries[planId]?.token;
    return isToken(token) ? token : null;
  }

  return {
    /** Remember a plan's token. Returns false for anything malformed. */
    save(planId, token) {
      if (!isPlanId(planId) || !isToken(token)) return false;

      const entries = load();
      entries[planId] = { token, at: Date.now() };

      // Keep only the newest MAX_PLANS plans.
      const newest = Object.entries(entries)
        .filter(([id, entry]) => isPlanId(id) && isToken(entry?.token))
        .sort((a, b) => (b[1].at || 0) - (a[1].at || 0))
        .slice(0, MAX_PLANS);

      persist(Object.fromEntries(newest));
      return true;
    },

    get(planId) {
      return entryToken(load(), planId);
    },

    /** Headers to merge into a request for this plan ({} when unknown). */
    headers(planId) {
      const token = entryToken(load(), planId);
      return token ? { [PLAN_ACCESS_HEADER]: token } : {};
    },

    forget(planId) {
      if (!isPlanId(planId)) return;
      const entries = load();
      delete entries[planId];
      persist(entries);
    },
  };
}
