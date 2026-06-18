import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import {
  buildDeepSeekMessages,
  createDeepSeekBrief,
  extractJsonCandidate,
  parseBriefContent
} from "../scripts/deepseek-brief.mjs";

test("buildDeepSeekMessages requests compact JSON", () => {
  const messages = buildDeepSeekMessages("some context", { goal: "fix bug" });
  assert.equal(messages[0].role, "system");
  assert.match(messages[0].content, /json/i);
  assert.match(messages[0].content, /candidate_files/);
  assert.equal(messages[1].role, "user");
});

test("createDeepSeekBrief sends json_object response format", async () => {
  let capturedBody;
  const fetchImpl = async (_url, options) => {
    capturedBody = JSON.parse(options.body);
    return {
      ok: true,
      async json() {
        return {
          model: "deepseek-v4-pro",
          choices: [
            {
              message: {
                content: JSON.stringify({
                  goal: "fix bug",
                  summary: "small summary",
                  task_type: "coding",
                  candidate_files: ["src/app.js"],
                  constraints: ["keep small"],
                  risks: ["none"],
                  acceptance_checks: ["npm test"],
                  next_action: "edit file"
                })
              }
            }
          ],
          usage: {
            prompt_tokens: 10,
            prompt_cache_hit_tokens: 2,
            completion_tokens: 5
          }
        };
      }
    };
  };

  const result = await createDeepSeekBrief("context", {
    apiKey: "test-key",
    fetchImpl,
    goal: "fix bug"
  });

  assert.deepEqual(capturedBody.response_format, { type: "json_object" });
  assert.match(capturedBody.messages[0].content, /json/i);
  assert.equal(capturedBody.model, "deepseek-v4-pro");
  assert.equal(capturedBody.extra_body.thinking.type, "disabled");
  assert.equal(result.brief.summary, "small summary");
});

test("extractJsonCandidate recovers JSON from fenced content", () => {
  const candidate = extractJsonCandidate("```json\n{\"goal\":\"x\"}\n```");
  assert.equal(candidate, "{\"goal\":\"x\"}");
});

test("parseBriefContent accepts wrapped JSON content", () => {
  const brief = parseBriefContent("Result:\n{\"goal\":\"x\",\"summary\":\"y\"}");
  assert.equal(brief.goal, "x");
  assert.equal(brief.summary, "y");
});

test("createDeepSeekBrief falls back to flash when pro returns empty content", async () => {
  const seenModels = [];
  const fetchImpl = async (_url, options) => {
    const body = JSON.parse(options.body);
    seenModels.push(body.model);
    if (body.model === "deepseek-v4-pro") {
      return {
        ok: true,
        async json() {
          return {
            model: "deepseek-v4-pro",
            choices: [{ message: { content: "" }, finish_reason: "stop" }]
          };
        }
      };
    }

    return {
      ok: true,
      async json() {
        return {
          model: "deepseek-v4-flash",
          choices: [
            {
              message: {
                content: "{\"goal\":\"fix bug\",\"summary\":\"flash worked\",\"task_type\":\"coding\",\"candidate_files\":[],\"constraints\":[],\"risks\":[],\"acceptance_checks\":[],\"next_action\":\"continue\"}"
              },
              finish_reason: "stop"
            }
          ]
        };
      }
    };
  };

  const result = await createDeepSeekBrief("context", {
    apiKey: "test-key",
    fetchImpl
  });

  assert.deepEqual(seenModels, ["deepseek-v4-pro", "deepseek-v4-flash"]);
  assert.equal(result.model, "deepseek-v4-flash");
  assert.equal(result.brief.summary, "flash worked");
});

test("createDeepSeekBrief fails closed after empty content on both models", async () => {
  const fetchImpl = async (_url, options) => {
    const body = JSON.parse(options.body);
    return {
      ok: true,
      async json() {
        return {
          model: body.model,
          choices: [{ message: { content: "" }, finish_reason: "stop" }]
        };
      }
    };
  };

  await assert.rejects(
    () => createDeepSeekBrief("context", { apiKey: "test-key", fetchImpl }),
    /empty content/
  );
});

test("deepseek-brief CLI dry run does not require an API key", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "codex-token-saver-"));
  const input = path.join(tmp, "sanitized.md");
  const out = path.join(tmp, "out");
  fs.writeFileSync(input, "# Sanitized\n\nSmall context", "utf8");

  const env = { ...process.env };
  delete env.DEEPSEEK_API_KEY;

  execFileSync(process.execPath, ["scripts/deepseek-brief.mjs", input, "--out", out], {
    cwd: process.cwd(),
    env,
    encoding: "utf8"
  });

  assert.equal(fs.existsSync(path.join(out, "deepseek-dry-run-report.json")), true);
  assert.equal(fs.existsSync(path.join(out, "context-brief.json")), false);
  const report = JSON.parse(fs.readFileSync(path.join(out, "deepseek-dry-run-report.json"), "utf8"));
  assert.equal(report.mode, "dry-run");
  assert.match(report.note, /No network call/);
});
