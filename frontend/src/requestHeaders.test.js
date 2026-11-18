import assert from "node:assert/strict";
import test from "node:test";

import { buildRequestHeaders } from "./requestHeaders.js";

test("caller headers are preserved (the bug: they were overwritten)", () => {
  const headers = buildRequestHeaders('{"a":1}', { "X-Plan-Token": "secret-123" });

  assert.equal(headers["X-Plan-Token"], "secret-123");
  assert.equal(headers["Content-Type"], "application/json");
});

test("caller headers are preserved on requests without a body", () => {
  const headers = buildRequestHeaders(undefined, { "X-Plan-Token": "secret-123" });

  assert.deepEqual(headers, { "X-Plan-Token": "secret-123" });
});

test("no body and no caller headers produce no headers (no needless CORS preflight)", () => {
  assert.deepEqual(buildRequestHeaders(undefined, undefined), {});
  assert.deepEqual(buildRequestHeaders(null, {}), {});
});

test("a caller-provided content type wins, in any letter case", () => {
  for (const name of ["Content-Type", "content-type", "CONTENT-TYPE"]) {
    const headers = buildRequestHeaders("raw", { [name]: "text/plain" });

    assert.equal(headers[name], "text/plain");
    assert.equal(Object.keys(headers).length, 1, `${name} must not be duplicated`);
  }
});

test("Headers instances and pair arrays are supported", () => {
  const fromInstance = buildRequestHeaders("{}", new Headers({ "X-Plan-Token": "t1" }));
  assert.equal(fromInstance["x-plan-token"], "t1");
  assert.equal(fromInstance["Content-Type"], "application/json");

  const fromPairs = buildRequestHeaders("{}", [["X-Plan-Token", "t2"]]);
  assert.equal(fromPairs["X-Plan-Token"], "t2");
});

test("the caller's object is never mutated", () => {
  const original = { "X-Plan-Token": "t" };

  buildRequestHeaders("{}", original);

  assert.deepEqual(original, { "X-Plan-Token": "t" });
});

test("several protected headers all survive", () => {
  const headers = buildRequestHeaders("{}", { "X-Plan-Token": "t", "X-Other": "o" });

  assert.equal(headers["X-Plan-Token"], "t");
  assert.equal(headers["X-Other"], "o");
});
