#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildBudgetReport } from "./token-budget.mjs";
import { ensureDir, estimateTokens, parseCliArgs, sanitizeText } from "./lib/context-utils.mjs";

const DEFAULT_MODEL = "deepseek-v4-pro";
const DEFAULT_FALLBACK_MODEL = "deepseek-v4-flash";
const API_URL = "https://api.deepseek.com/chat/completions";

export function buildDeepSeekMessages(context, options = {}) {
  const goal = options.goal ?? "Summarize sanitized context for a future Codex coding session.";
  return [
    {
      role: "system",
      content: [
        "Return valid json only.",
        "The word json is intentional.",
        "Return exactly one JSON object and nothing else.",
        "Do not use markdown fences.",
        "Use this JSON shape:",
        '{"goal":"","summary":"","task_type":"","candidate_files":[],"constraints":[],"risks":[],"acceptance_checks":[],"next_action":""}',
        "Keep output compact. Do not invent facts. Do not include secrets."
      ].join("\n")
    },
    {
      role: "user",
      content: JSON.stringify({
        goal,
        sanitized_context: sanitizeText(context)
      })
    }
  ];
}

export function extractJsonCandidate(content) {
  const trimmed = String(content ?? "").trim();
  if (!trimmed) return "";
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) return trimmed;

  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenced?.[1]) {
    return fenced[1].trim();
  }

  const firstBrace = trimmed.indexOf("{");
  const lastBrace = trimmed.lastIndexOf("}");
  if (firstBrace >= 0 && lastBrace > firstBrace) {
    return trimmed.slice(firstBrace, lastBrace + 1).trim();
  }

  return trimmed;
}

export function parseBriefContent(content) {
  const candidate = extractJsonCandidate(content);
  if (!candidate) {
    throw new Error("DeepSeek returned empty content");
  }

  try {
    return JSON.parse(candidate);
  } catch (error) {
    throw new Error(`DeepSeek returned invalid JSON: ${error.message}`);
  }
}

function buildRequestBody(context, options = {}) {
  return {
    model: options.model ?? DEFAULT_MODEL,
    messages: buildDeepSeekMessages(context, options),
    response_format: { type: "json_object" },
    max_tokens: Number(options.maxTokens ?? 900),
    temperature: 0,
    extra_body: { thinking: { type: "disabled" } }
  };
}

async function runDeepSeekRequest(context, options = {}) {
  const apiKey = options.apiKey ?? process.env.DEEPSEEK_API_KEY;
  if (!apiKey) {
    throw new Error("DEEPSEEK_API_KEY is missing");
  }

  const body = buildRequestBody(context, options);
  const fetchImpl = options.fetchImpl ?? globalThis.fetch;
  const response = await fetchImpl(API_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body)
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`DeepSeek request failed: ${response.status} ${errorText}`);
  }

  const payload = await response.json();
  return {
    body,
    payload,
    content: payload?.choices?.[0]?.message?.content ?? "",
    finishReason: payload?.choices?.[0]?.finish_reason ?? null,
    model: payload?.model ?? body.model
  };
}

function isRecoverableBriefError(error, attempt) {
  const message = String(error?.message ?? "");
  if (!message) return false;
  if (/empty content/i.test(message)) return true;
  if (/invalid JSON/i.test(message)) return true;
  if (attempt.finishReason === "length") return true;
  return false;
}

function normalizeAttemptOptions(options = {}, overrides = {}) {
  return {
    ...options,
    ...overrides,
    maxTokens: overrides.maxTokens ?? options.maxTokens
  };
}

export async function createDeepSeekBrief(context, options = {}) {
  const primaryOptions = normalizeAttemptOptions(options, {
    model: options.model ?? DEFAULT_MODEL,
    maxTokens: Number(options.maxTokens ?? 900)
  });

  const attempts = [primaryOptions];
  if (options.allowFallback !== false && primaryOptions.model !== DEFAULT_FALLBACK_MODEL) {
    attempts.push(
      normalizeAttemptOptions(options, {
        model: options.fallbackModel ?? DEFAULT_FALLBACK_MODEL,
        maxTokens: Math.max(Number(options.maxTokens ?? 900), 1200)
      })
    );
  }

  let lastFailure;
  for (const attemptOptions of attempts) {
    const attempt = await runDeepSeekRequest(context, attemptOptions);
    try {
      const brief = parseBriefContent(attempt.content);
      return {
        brief,
        request: attempt.body,
        usage: attempt.payload?.usage ?? null,
        model: attempt.model,
        finishReason: attempt.finishReason
      };
    } catch (error) {
      lastFailure = error;
      if (!isRecoverableBriefError(error, attempt)) {
        throw error;
      }
    }
  }

  throw lastFailure ?? new Error("DeepSeek brief generation failed");
}

export function buildFailureNote(error, options = {}) {
  const model = options.model ?? DEFAULT_MODEL;
  const fallbackModel = options.fallbackModel ?? DEFAULT_FALLBACK_MODEL;
  return [
    "## Nota DeepSeek",
    `Se envio solo contexto sanitizado.`,
    `La API no devolvio un JSON usable con \`${model}\`${model === fallbackModel ? "." : ` ni con \`${fallbackModel}\`.`}`,
    `Motivo final: ${error.message}`,
    "La decision final puede hacerse localmente con Codex usando el mismo contexto sanitizado.",
    ""
  ].join("\n");
}

export function briefToMarkdown(result) {
  const brief = result.brief ?? result;
  const list = (value) => (Array.isArray(value) && value.length ? value.map((item) => `- ${item}`).join("\n") : "- none");
  return [
    "# Context Brief",
    "",
    `Model: ${result.model ?? "unknown"}`,
    result.finishReason ? `Finish reason: ${result.finishReason}` : null,
    "",
    "## Goal",
    brief.goal || "Not provided",
    "",
    "## Summary",
    brief.summary || "No summary returned",
    "",
    "## Task Type",
    brief.task_type || "unknown",
    "",
    "## Candidate Files",
    list(brief.candidate_files),
    "",
    "## Constraints",
    list(brief.constraints),
    "",
    "## Risks",
    list(brief.risks),
    "",
    "## Acceptance Checks",
    list(brief.acceptance_checks),
    "",
    "## Next Action",
    brief.next_action || "Review the brief and continue with targeted Codex work",
    ""
  ].filter(Boolean).join("\n");
}

export function failureToMarkdown(error, options = {}) {
  return buildFailureNote(error, options);
}

export function successSummary(result, context) {
  return {
    output_dir: result.outputDir,
    model: result.model,
    finish_reason: result.finishReason,
    estimated_input_tokens: estimateTokens(context),
    usage: result.usage
  };
}

function usage() {
  return [
    "Usage: node scripts/deepseek-brief.mjs <sanitized-context.md> [--out dir] [--goal text] [--model deepseek-v4-pro]",
    "",
    "If DEEPSEEK_API_KEY is missing, no network call is made and only a dry-run budget report is written."
  ].join("\n");
}

function writeDryRun(inputPath, outDir, context) {
  ensureDir(outDir);
  const report = {
    mode: "dry-run",
    reason: "DEEPSEEK_API_KEY is missing",
    input: path.resolve(inputPath),
    estimated_tokens: estimateTokens(context),
    note: "No network call was made."
  };
  const jsonPath = path.join(outDir, "deepseek-dry-run-report.json");
  const mdPath = path.join(outDir, "deepseek-dry-run-report.md");
  fs.writeFileSync(jsonPath, JSON.stringify(report, null, 2), "utf8");
  fs.writeFileSync(
    mdPath,
    [
      "# DeepSeek Dry Run Report",
      "",
      `Input: ${report.input}`,
      `Estimated tokens: ${report.estimated_tokens}`,
      "",
      "No network call was made because `DEEPSEEK_API_KEY` is missing.",
      ""
    ].join("\n"),
    "utf8"
  );
  return report;
}

async function main() {
  const args = parseCliArgs(process.argv.slice(2));
  if (args.help || args._.length !== 1) {
    console.log(usage());
    process.exit(args.help ? 0 : 1);
  }

  const inputPath = path.resolve(args._[0]);
  const outDir = path.resolve(args.out ?? ".codex-token-saver");
  const context = fs.readFileSync(inputPath, "utf8");

  if (!process.env.DEEPSEEK_API_KEY || args["dry-run"]) {
    const report = writeDryRun(inputPath, outDir, context);
    console.log(JSON.stringify(report, null, 2));
    return;
  }

  ensureDir(outDir);
  const budget = buildBudgetReport([inputPath]);
  fs.writeFileSync(path.join(outDir, "input-budget.json"), JSON.stringify(budget, null, 2), "utf8");
  const options = {
    goal: args.goal,
    model: args.model ?? DEFAULT_MODEL,
    fallbackModel: args["fallback-model"] ?? DEFAULT_FALLBACK_MODEL,
    maxTokens: args["max-tokens"]
  };

  try {
    const result = await createDeepSeekBrief(context, options);
    result.outputDir = outDir;
    fs.writeFileSync(path.join(outDir, "context-brief.json"), JSON.stringify(result, null, 2), "utf8");
    fs.writeFileSync(path.join(outDir, "context-brief.md"), briefToMarkdown(result), "utf8");
    console.log(JSON.stringify(successSummary(result, context), null, 2));
  } catch (error) {
    fs.writeFileSync(
      path.join(outDir, "context-brief-failure.md"),
      failureToMarkdown(error, options),
      "utf8"
    );
    fs.writeFileSync(
      path.join(outDir, "context-brief-failure.json"),
      JSON.stringify(
        {
          error: error.message,
          input: inputPath,
          model: options.model,
          fallback_model: options.fallbackModel,
          estimated_input_tokens: estimateTokens(context)
        },
        null,
        2
      ),
      "utf8"
    );
    throw error;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main().catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
