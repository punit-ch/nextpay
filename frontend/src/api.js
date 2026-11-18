import { browserStorage, createPlanAccessStore } from "./planAccess";
import { buildRequestHeaders } from "./requestHeaders";

// Backend location comes from the environment (see frontend/.env.example).
export const API_URL = (
  import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"
).replace(/\/+$/, "");

const planAccessStore = createPlanAccessStore(browserStorage());

/**
 * Headers that unlock one plan on the backend ({} when no token is known).
 * Pass them as `headers` to every request that reads or changes that plan.
 */
export function planAccessHeaders(planId) {
  return planAccessStore.headers(planId);
}

/**
 * Remember the access token the backend returned when the plan was created.
 * Returns false when the plan id or token is malformed.
 */
export function storePlanAccessToken(planId, token) {
  return planAccessStore.save(planId, token);
}

/**
 * JSON request helper with readable errors.
 * Network failures and API errors both surface as `Error(message)`.
 *
 * Caller-supplied `headers` are merged with the JSON content type, never
 * replaced, so protected endpoints keep receiving their access headers.
 */
export async function apiRequest(path, options = {}) {
  const { body, headers: callerHeaders, ...rest } = options;
  const headers = buildRequestHeaders(body, callerHeaders);

  let response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      cache: "no-store", // payment state must never come from a browser cache
      ...rest,
      headers,
      body,
    });
  } catch (err) {
    // A request we cancelled on purpose is not a network failure.
    if (err?.name === "AbortError") throw err;
    throw new Error(
      "Could not reach NEXTPAY. Check your connection and that the backend is running.",
    );
  }

  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    const detail = data?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item) => item.msg).join(" ")
          : null;
    const error = new Error(message || `Request failed (HTTP ${response.status}).`);
    error.status = response.status;
    throw error;
  }

  return data;
}

/** Only ever send the browser to PayPal over HTTPS. */
export function isPayPalUrl(value) {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      (url.hostname === "paypal.com" || url.hostname.endsWith(".paypal.com"))
    );
  } catch {
    return false;
  }
}
