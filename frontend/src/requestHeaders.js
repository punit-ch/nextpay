// Pure helper (no network, no import.meta) so header merging is unit-tested.

function toPlainObject(headers) {
  if (!headers) return {};

  // fetch Headers instance
  if (typeof Headers !== "undefined" && headers instanceof Headers) {
    return Object.fromEntries(headers.entries());
  }

  // [[name, value], ...]
  if (Array.isArray(headers)) {
    return Object.fromEntries(headers);
  }

  return { ...headers };
}

/**
 * Merge caller-supplied headers with the JSON content type.
 *
 * - Caller headers are ALWAYS kept (for example a plan access token).
 * - Content-Type: application/json is added only when there is a body and
 *   the caller did not set a content type (compared case-insensitively).
 * - A caller-supplied value always wins over our default.
 */
export function buildRequestHeaders(body, callerHeaders) {
  const merged = toPlainObject(callerHeaders);

  const callerSetContentType = Object.keys(merged).some(
    (name) => name.toLowerCase() === "content-type",
  );

  if (body && !callerSetContentType) {
    merged["Content-Type"] = "application/json";
  }

  return merged;
}
