#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  collectFiles,
  ensureDir,
  parseCliArgs,
  readTextFileForContext,
  summarizeEntries,
  toMarkdownContext
} from "./lib/context-utils.mjs";

function usage() {
  return [
    "Usage: node scripts/sanitize-context.mjs <file-or-dir...> [--out path] [--max-file-bytes 80000] [--max-files 500]",
    "",
    "Writes sanitized markdown to --out, or stdout when --out is omitted."
  ].join("\n");
}

export function sanitizeInputs(inputs, options = {}) {
  const files = collectFiles(inputs, { maxFiles: options.maxFiles });
  const entries = files.map((filePath) =>
    readTextFileForContext(filePath, { maxFileBytes: options.maxFileBytes })
  );
  return {
    markdown: toMarkdownContext(entries, { root: options.root ?? process.cwd() }),
    summary: summarizeEntries(entries),
    entries
  };
}

async function main() {
  const args = parseCliArgs(process.argv.slice(2));
  if (args.help || args._.length === 0) {
    console.log(usage());
    process.exit(args.help ? 0 : 1);
  }

  const maxFileBytes = Number(args["max-file-bytes"] ?? 80_000);
  const maxFiles = Number(args["max-files"] ?? 500);
  const result = sanitizeInputs(args._, { maxFileBytes, maxFiles });

  if (args.out) {
    const outPath = path.resolve(args.out);
    ensureDir(path.dirname(outPath));
    fs.writeFileSync(outPath, result.markdown, "utf8");
    console.log(
      JSON.stringify(
        {
          output: outPath,
          ...result.summary
        },
        null,
        2
      )
    );
    return;
  }

  process.stdout.write(result.markdown);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main().catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
