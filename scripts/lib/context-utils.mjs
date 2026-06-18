import fs from "node:fs";
import path from "node:path";

export const DEFAULT_EXCLUDES = new Set([
  ".git",
  "node_modules",
  "dist",
  "build",
  "coverage",
  ".next",
  ".turbo",
  ".cache",
  ".codex-token-saver"
]);

export const BINARY_EXTENSIONS = new Set([
  ".7z",
  ".avi",
  ".bin",
  ".bmp",
  ".db",
  ".dll",
  ".doc",
  ".docx",
  ".exe",
  ".gif",
  ".ico",
  ".jpeg",
  ".jpg",
  ".mov",
  ".mp3",
  ".mp4",
  ".pdf",
  ".png",
  ".ppt",
  ".pptx",
  ".sqlite",
  ".webp",
  ".xls",
  ".xlsx",
  ".zip"
]);

export const SENSITIVE_BASENAMES = new Set([
  ".env",
  ".env.local",
  ".env.production",
  "auth.json",
  "credentials.json",
  "id_rsa",
  "id_ed25519",
  "known_hosts",
  "secrets.json"
]);

export function estimateTokens(text) {
  return Math.ceil(String(text ?? "").length / 4);
}

export function isProbablyBinary(buffer) {
  if (!buffer || buffer.length === 0) return false;
  const sample = buffer.subarray(0, Math.min(buffer.length, 4096));
  let suspicious = 0;
  for (const byte of sample) {
    if (byte === 0) return true;
    const isControl = byte < 7 || (byte > 13 && byte < 32);
    if (isControl) suspicious += 1;
  }
  return suspicious / sample.length > 0.25;
}

export function isSensitivePath(filePath) {
  const base = path.basename(filePath).toLowerCase();
  if (SENSITIVE_BASENAMES.has(base)) return true;
  if (/(\.pem|\.key|\.p12|\.pfx|\.kdbx)$/i.test(base)) return true;
  if (/(secret|credential|token|private-key)/i.test(base)) return true;
  return false;
}

export function shouldSkipDir(dirName) {
  return DEFAULT_EXCLUDES.has(dirName);
}

export function collectFiles(inputs, options = {}) {
  const files = [];
  const maxFiles = options.maxFiles ?? 500;

  function visit(target) {
    if (files.length >= maxFiles) return;
    const resolved = path.resolve(target);
    if (!fs.existsSync(resolved)) return;
    const stat = fs.statSync(resolved);
    if (stat.isDirectory()) {
      const base = path.basename(resolved);
      if (shouldSkipDir(base)) return;
      for (const entry of fs.readdirSync(resolved, { withFileTypes: true })) {
        if (entry.isDirectory() && shouldSkipDir(entry.name)) continue;
        visit(path.join(resolved, entry.name));
        if (files.length >= maxFiles) break;
      }
      return;
    }
    if (!stat.isFile()) return;
    files.push(resolved);
  }

  for (const input of inputs) visit(input);
  return files;
}

export function sanitizeText(input) {
  let text = String(input ?? "");

  text = text.replace(
    /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----/g,
    "[REDACTED_PEM_PRIVATE_KEY]"
  );

  text = text.replace(
    /^(?<prefix>\s*[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASS|AUTH|CREDENTIAL)[A-Z0-9_]*\s*=\s*)(?<value>.+)$/gim,
    "$<prefix>[REDACTED_SECRET]"
  );

  text = text.replace(
    /(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._~+/=-]{12,}/gi,
    "$1[REDACTED_BEARER_TOKEN]"
  );

  text = text.replace(
    /\b(?:sk|pk|rk|ds|org|proj)-[A-Za-z0-9_-]{16,}\b/g,
    "[REDACTED_API_KEY]"
  );

  text = text.replace(
    /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g,
    "[REDACTED_JWT]"
  );

  text = text.replace(
    /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g,
    "[REDACTED_EMAIL]"
  );

  text = text.replace(
    /[A-Za-z]:\\Users\\[^\\\s]+/g,
    "C:\\Users\\[USER]"
  );

  text = text.replace(
    /\/home\/[^/\s]+/g,
    "/home/[USER]"
  );

  text = text.replace(
    /(password|passwd|pwd)\s*[:=]\s*["']?[^"',\s}]+/gi,
    "$1=[REDACTED_PASSWORD]"
  );

  return text;
}

export function readTextFileForContext(filePath, options = {}) {
  const maxFileBytes = options.maxFileBytes ?? 80_000;
  const ext = path.extname(filePath).toLowerCase();
  const stat = fs.statSync(filePath);

  if (isSensitivePath(filePath)) {
    return {
      filePath,
      skipped: true,
      reason: "sensitive-path",
      bytes: stat.size,
      tokens: 0,
      text: ""
    };
  }

  if (BINARY_EXTENSIONS.has(ext)) {
    return {
      filePath,
      skipped: true,
      reason: "binary-extension",
      bytes: stat.size,
      tokens: 0,
      text: ""
    };
  }

  const buffer = fs.readFileSync(filePath);
  if (isProbablyBinary(buffer)) {
    return {
      filePath,
      skipped: true,
      reason: "binary-content",
      bytes: stat.size,
      tokens: 0,
      text: ""
    };
  }

  const wasTruncated = buffer.length > maxFileBytes;
  const raw = buffer.subarray(0, maxFileBytes).toString("utf8");
  const text = sanitizeText(raw);
  return {
    filePath,
    skipped: false,
    reason: wasTruncated ? "truncated" : "included",
    bytes: stat.size,
    tokens: estimateTokens(text),
    text,
    wasTruncated
  };
}

export function parseCliArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith("--")) {
      args._.push(arg);
      continue;
    }
    const key = arg.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key] = true;
      continue;
    }
    args[key] = next;
    i += 1;
  }
  return args;
}

export function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

export function toMarkdownContext(entries, options = {}) {
  const root = options.root ? path.resolve(options.root) : process.cwd();
  const chunks = ["# Sanitized Context", ""];
  for (const entry of entries) {
    const rel = path.relative(root, entry.filePath) || path.basename(entry.filePath);
    if (entry.skipped) {
      chunks.push(`## ${rel}`, "", `Skipped: ${entry.reason}`, "");
      continue;
    }
    chunks.push(`## ${rel}`, "", "```text", entry.text.trimEnd(), "```", "");
  }
  return chunks.join("\n");
}

export function summarizeEntries(entries) {
  const included = entries.filter((entry) => !entry.skipped);
  const skipped = entries.filter((entry) => entry.skipped);
  return {
    files_seen: entries.length,
    files_included: included.length,
    files_skipped: skipped.length,
    estimated_tokens: included.reduce((sum, entry) => sum + entry.tokens, 0),
    skipped_by_reason: skipped.reduce((acc, entry) => {
      acc[entry.reason] = (acc[entry.reason] ?? 0) + 1;
      return acc;
    }, {})
  };
}
