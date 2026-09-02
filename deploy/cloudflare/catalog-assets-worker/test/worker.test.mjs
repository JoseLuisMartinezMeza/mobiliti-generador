import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import worker, {
  CACHE_CONTROL,
  PRODUCTION_ORIGIN,
} from "../src/index.js";

const SHA256 = "a".repeat(64);
const KEY = `${SHA256}.png`;

function objeto_valido(overrides = {}) {
  return {
    size: 3,
    body: "png",
    httpMetadata: {
      contentType: "image/png",
      cacheControl: CACHE_CONTROL,
    },
    customMetadata: { sha256: SHA256 },
    httpEtag: '"r2-etag"',
    ...overrides,
  };
}

function entorno(object = objeto_valido()) {
  const calls = [];
  const head_calls = [];
  return {
    calls,
    head_calls,
    env: {
      ALLOWED_ORIGIN: PRODUCTION_ORIGIN,
      CATALOG_ASSETS: {
        async get(key) {
          calls.push(key);
          return object;
        },
        async head(key) {
          head_calls.push(key);
          return object;
        },
      },
    },
  };
}

async function solicitar(url, init, object) {
  const state = entorno(object);
  const response = await worker.fetch(new Request(url, init), state.env);
  return { response, ...state };
}

function assert_no_store(response) {
  assert.equal(response.headers.get("Cache-Control"), "no-store");
}

test("el Worker sirve un asset válido con CORS determinista y cache inmutable", async () => {
  const { response, calls } = await solicitar(
    `https://assets.example.workers.dev/${KEY}`,
    { headers: { Origin: PRODUCTION_ORIGIN } },
  );

  assert.equal(response.status, 200);
  assert.equal(await response.text(), "png");
  assert.deepEqual(calls, [KEY]);
  assert.equal(response.headers.get("Content-Type"), "image/png");
  assert.equal(response.headers.get("Content-Length"), "3");
  assert.equal(response.headers.get("ETag"), '"r2-etag"');
  assert.equal(response.headers.get("Cache-Control"), CACHE_CONTROL);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), PRODUCTION_ORIGIN);
  assert.equal(response.headers.get("Vary"), null);
});

test("el handler directo HEAD valida metadatos sin devolver cuerpo", async () => {
  const { response, calls, head_calls } = await solicitar(
    `https://assets.example.workers.dev/${KEY}`,
    { method: "HEAD" },
  );

  assert.equal(response.status, 200);
  assert.equal(await response.text(), "");
  assert.deepEqual(calls, []);
  assert.deepEqual(head_calls, [KEY]);
  assert.equal(response.headers.get("Content-Length"), "3");
  assert.equal(response.headers.get("Cache-Control"), CACHE_CONTROL);
});

test("las rutas no canónicas y cualquier query se rechazan antes de consultar R2", async () => {
  const invalid_urls = [
    `https://assets.example.workers.dev/${KEY}?v=1`,
    `https://assets.example.workers.dev/${SHA256}.PNG`,
    `https://assets.example.workers.dev/a/${KEY}`,
    `https://assets.example.workers.dev/${KEY}%23fragment`,
    "https://assets.example.workers.dev/",
  ];

  for (const url of invalid_urls) {
    const { response, calls } = await solicitar(url);
    assert.equal(response.status, 400, url);
    assert_no_store(response);
    assert.deepEqual(calls, [], url);
  }
});

test("un delimitador query vacío se rechaza antes de consultar R2", async () => {
  const { response, calls } = await solicitar(
    `https://assets.example.workers.dev/${KEY}?`,
  );

  assert.equal(response.status, 400);
  assert_no_store(response);
  assert.deepEqual(calls, []);
});

test("los métodos fuera de GET, HEAD y OPTIONS no consultan R2", async () => {
  const { response, calls } = await solicitar(
    `https://assets.example.workers.dev/${KEY}`,
    { method: "POST" },
  );

  assert.equal(response.status, 405);
  assert.equal(response.headers.get("Allow"), "GET, HEAD, OPTIONS");
  assert_no_store(response);
  assert.deepEqual(calls, []);
});

test("el preflight autorizado es no-store y el origen distinto no queda autorizado", async () => {
  const allowed = await solicitar(`https://assets.example.workers.dev/${KEY}`, {
    method: "OPTIONS",
    headers: { Origin: PRODUCTION_ORIGIN },
  });
  assert.equal(allowed.response.status, 204);
  assert_no_store(allowed.response);
  assert.equal(
    allowed.response.headers.get("Access-Control-Allow-Origin"),
    PRODUCTION_ORIGIN,
  );
  assert.equal(allowed.response.headers.get("Access-Control-Allow-Methods"), "GET, HEAD");
  assert.deepEqual(allowed.calls, []);

  const denied = await solicitar(`https://assets.example.workers.dev/${KEY}`, {
    method: "OPTIONS",
    headers: { Origin: "https://evil.example" },
  });
  assert.equal(denied.response.status, 403);
  assert_no_store(denied.response);
  assert.equal(denied.response.headers.get("Access-Control-Allow-Origin"), null);
  assert.deepEqual(denied.calls, []);
});

test("una solicitud simple de origen no autorizado no recibe ACAO para ese origen", async () => {
  const { response } = await solicitar(`https://assets.example.workers.dev/${KEY}`, {
    headers: { Origin: "https://evil.example" },
  });

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), PRODUCTION_ORIGIN);
  assert.notEqual(response.headers.get("Access-Control-Allow-Origin"), "https://evil.example");
  assert.equal(response.headers.get("Vary"), null);
});

test("un objeto ausente, corrupto o con metadatos no exactos falla cerrado sin cachear", async () => {
  const corruptions = [
    null,
    objeto_valido({ size: 0 }),
    objeto_valido({ httpMetadata: { contentType: "image/jpeg", cacheControl: CACHE_CONTROL } }),
    objeto_valido({ customMetadata: { sha256: "b".repeat(64) } }),
    objeto_valido({ httpMetadata: { contentType: "image/png", cacheControl: "public, max-age=60" } }),
    objeto_valido({ httpEtag: "" }),
  ];

  for (const object of corruptions) {
    const { response, calls } = await solicitar(
      `https://assets.example.workers.dev/${KEY}`,
      undefined,
      object,
    );
    assert.ok([404, 502].includes(response.status));
    assert_no_store(response);
    assert.deepEqual(calls, [KEY]);
  }
});

test("un error de R2 se reduce a una respuesta estructurada no cacheable", async () => {
  const state = entorno();
  state.env.CATALOG_ASSETS.get = async () => {
    throw new Error("detalle interno");
  };

  const response = await worker.fetch(
    new Request(`https://assets.example.workers.dev/${KEY}`),
    state.env,
  );
  assert.equal(response.status, 502);
  assert_no_store(response);
  assert.equal(await response.text(), "Asset unavailable");
});

test("sólo el 2xx validado es inmutable; errores y preflight son no-store", async () => {
  const valid = await solicitar(`https://assets.example.workers.dev/${KEY}`);
  assert.equal(valid.response.status, 200);
  assert.equal(valid.response.headers.get("Cache-Control"), CACHE_CONTROL);

  const failures = [
    await solicitar(`https://assets.example.workers.dev/${KEY}?`),
    await solicitar(`https://assets.example.workers.dev/${KEY}`, { method: "POST" }),
    await solicitar(`https://assets.example.workers.dev/${KEY}`, {
      method: "OPTIONS",
      headers: { Origin: "https://evil.example" },
    }),
    await solicitar(`https://assets.example.workers.dev/${KEY}`, undefined, null),
    await solicitar(
      `https://assets.example.workers.dev/${KEY}`,
      undefined,
      objeto_valido({ httpEtag: "" }),
    ),
  ];

  for (const { response } of failures) {
    assert.ok(response.status >= 400, `unexpected status ${response.status}`);
    assert_no_store(response);
    assert.notEqual(response.headers.get("Cache-Control"), CACHE_CONTROL);
  }

  const preflight = await solicitar(`https://assets.example.workers.dev/${KEY}`, {
    method: "OPTIONS",
    headers: { Origin: PRODUCTION_ORIGIN },
  });
  assert.equal(preflight.response.status, 204);
  assert_no_store(preflight.response);
});

test("la configuración Free usa el límite CPU administrado por Cloudflare", async () => {
  const config = await readFile(new URL("../wrangler.toml", import.meta.url), "utf8");

  assert.match(config, /^name\s*=\s*"mobiliti-catalog-assets"$/m);
  assert.match(config, /^compatibility_date\s*=\s*"2026-09-01"$/m);
  assert.match(config, /^workers_dev\s*=\s*true$/m);
  assert.match(config, /\[cache\]\s*\r?\nenabled\s*=\s*true/m);
  assert.doesNotMatch(config, /\[limits\]|cpu_ms/i);
  assert.match(config, /\[\[r2_buckets\]\][\s\S]*?binding\s*=\s*"CATALOG_ASSETS"[\s\S]*?bucket_name\s*=\s*"catalog-assets"/m);
  assert.match(config, /^ALLOWED_ORIGIN\s*=\s*"https:\/\/web-lemon-one-45\.vercel\.app"$/m);
  assert.doesNotMatch(config, /r2\.dev|route\s*=|zones|account_id/i);
});

test("el código no contiene operaciones mutantes ni usa caches.default", async () => {
  const source = await readFile(new URL("../src/index.js", import.meta.url), "utf8");

  assert.doesNotMatch(source, /CATALOG_ASSETS\.(?:put|delete|list)\s*\(/);
  assert.doesNotMatch(source, /caches\.default/);
});

test("el README describe la validación sólo en fill o miss y la semántica real de HEAD", async () => {
  const readme = await readFile(new URL("../README.md", import.meta.url), "utf8");

  assert.match(readme, /fill\/miss/i);
  assert.match(readme, /HIT[^\n]*representación ya validada/i);
  assert.match(readme, /`GET` y `HEAD`[^\n]*comparten una sola entrada/i);
  assert.match(readme, /`HEAD` frío[\s\S]*?`GET`[\s\S]*?antes del handler/i);
  assert.doesNotMatch(readme, /cada HIT[^\n]*valida R2/i);
});
