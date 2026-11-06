// Backend location comes from the environment (see frontend/.env.example).
export const API_URL = (
  import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"
).replace(/\/+$/, "");

/**
 * JSON request helper with readable errors.
 * Network failures and API errors both surface as `Error(message)`.
 */
export async function apiRequest(path, options = {}) {
  const { body, ...rest } = options;
  const headers = body ? { "Content-Type": "application/json" } : {};

  let response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...rest, headers, body });
  } catch {
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
    throw new Error(message || `Request failed (HTTP ${response.status}).`);
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
