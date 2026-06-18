#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  collectFiles,
  estimateTokens,
  isProbablyBinary,
  isSensitivePath,
  parseCliArgs,
  readTextFileForContext
} from "./lib/context-utils.mjs";

function usage() {
  return [
    "Usage: node scripts/token-budget.mjs <file-or-dir...> [--json] [--max-files 500]",
    "",
    "Estimates context size and recommends load/summarize/exclude."
  ].join("\n");
}

export function buildBudgetReport(inputs, options = {}) {
  const maxFiles = options.maxFiles ?? 500;
  const files = collectFiles(inputs, { maxFiles });
  const rows = files.map((filePath) => {
    const stat = fs.statSync(filePath);
    const ext = path.extname(filePath).toLowerCase();
    if (isSensitivePath(filePath)) {
      return { file: filePath, bytes: stat.size, tokens: 0, action: "exclude", reason: "sensitive-path" };
    }
    const sample = fs.readFileSync(filePath).subarray(0, 4096);
    if (isProbablyBinary(sample)) {
      return { file: filePath, bytes: stat.size, tokens: 0, action: "exclude", reason: "binary" };
    }
    const entry = readTextFileForContext(filePath, { maxFileBytes: 120_000 });
    const tokens = entry.tokens || estimateTokens(entry.text);
    let action = "load";
    if (tokens > 12_000 || stat.size > 80_000) action = "summarize";
    if (tokens > 40_000 || stat.size > 250_000) action = "chunk-or-brief";
    return { file: filePath, bytes: stat.size, ext, tokens, action, reason: entry.reason };
  });

  const totals = rows.reduce(
    (acc, row) => {
      acc.files += 1;
      acc.bytes += row.bytes;
      acc.tokens += row.tokens;
      acc.actions[row.action] = (acc.actions[row.action] ?? 0) + 1;
      return acc;
    },
    { files: 0, bytes: 0, tokens: 0, actions: {} }
  );

  return { totals, rows };
}

function toTextReport(report) {
  const lines = [
    "# Token Budget Report",
    "",
    `Files: ${report.totals.files}`,
    `Bytes: ${report.totals.bytes}`,
    `Estimated tokens: ${report.totals.tokens}`,
    `Actions: ${JSON.stringify(report.totals.actions)}`,
    "",
    "| Action | Tokens | Bytes | File | Reason |",
    "|---|---:|---:|---|---|"
  ];

  for (const row of report.rows.sort((a, b) => b.tokens - a.tokens)) {
    lines.push(
      `| ${row.action} | ${row.tokens} | ${row.bytes} | ${row.file.replaceAll("\\", "/")} | ${row.reason} |`
    );
  }
  return `${lines.join("\n")}\n`;
}

async function main() {
  const args = parseCliArgs(process.argv.slice(2));
  if (args.help || args._.length === 0) {
    console.log(usage());
    process.exit(args.help ? 0 : 1);
  }

  const report = buildBudgetReport(args._, { maxFiles: Number(args["max-files"] ?? 500) });
  if (args.json) {
    console.log(JSON.stringify(report, null, 2));
    return;
  }
  process.stdout.write(toTextReport(report));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main().catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
