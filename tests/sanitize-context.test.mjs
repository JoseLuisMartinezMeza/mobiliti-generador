import assert from "node:assert/strict";
import { test } from "node:test";
import path from "node:path";
import {
  isProbablyBinary,
  readTextFileForContext,
  sanitizeText
} from "../scripts/lib/context-utils.mjs";
import { sanitizeInputs } from "../scripts/sanitize-context.mjs";

const fixtureDir = path.resolve("tests/fixtures/context");

test("sanitizeText redacts common secret and identity patterns", () => {
  const raw = [
    "OPENAI_API_KEY=test-secret-value-1234567890",
    "Authorization: Bearer TEST_BEARER_TOKEN",
    "pepe@example.com",
    "C:\\Users\\pepem\\Downloads\\thing",
    "password: supersecret",
    "JWT_PLACEHOLDER",
    "-----BEGIN " + "PRIVATE KEY-----\nabc\n-----END " + "PRIVATE KEY-----"
  ].join("\n");

  const clean = sanitizeText(raw);

  assert.match(clean, /\[REDACTED_SECRET\]/);
  assert.match(clean, /\[REDACTED_BEARER_TOKEN\]/);
  assert.match(clean, /\[REDACTED_EMAIL\]/);
  assert.match(clean, /C:\\Users\\\[USER\]/);
  assert.match(clean, /\[REDACTED_PASSWORD\]/);
  assert.match(clean, /\[REDACTED_JWT\]/);
  assert.match(clean, /\[REDACTED_PEM_PRIVATE_KEY\]/);
  assert.doesNotMatch(clean, new RegExp("supersecret|pepe@example.com|BEGIN " + "PRIVATE KEY"));
});

test("readTextFileForContext skips sensitive files", () => {
  const envEntry = readTextFileForContext(path.join(fixtureDir, ".env"));
  const keyEntry = readTextFileForContext(path.join(fixtureDir, "private.key"));

  assert.equal(envEntry.skipped, true);
  assert.equal(envEntry.reason, "sensitive-path");
  assert.equal(keyEntry.skipped, true);
  assert.equal(keyEntry.reason, "sensitive-path");
});

test("isProbablyBinary detects null-byte content", () => {
  assert.equal(isProbablyBinary(Buffer.from([65, 0, 66])), true);
  assert.equal(isProbablyBinary(Buffer.from("plain text")), false);
});

test("sanitizeInputs returns markdown without raw secrets", () => {
  const result = sanitizeInputs([fixtureDir], { root: process.cwd() });

  assert.equal(result.summary.files_seen, 3);
  assert.equal(result.summary.files_included, 1);
  assert.equal(result.summary.files_skipped, 2);
  assert.match(result.markdown, /Sanitized Context/);
  assert.doesNotMatch(result.markdown, /test-secret-value-1234567890|pepe@example.com|supersecret/);
  assert.match(result.markdown, /\[REDACTED_SECRET\]/);
});
