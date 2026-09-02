export const CACHE_CONTROL = "public, max-age=31536000, immutable";
export const PRODUCTION_ORIGIN = "https://web-lemon-one-45.vercel.app";

const ASSET_PATH = /^\/([a-f0-9]{64})\.(png|jpg|jpeg|webp)$/;
const MIME_TYPES = Object.freeze({
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
});
const MAX_ASSET_BYTES = 25 * 1024 * 1024;
const ALLOWED_METHODS = "GET, HEAD, OPTIONS";

function no_store(status, body, headers = {}) {
  return new Response(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "text/plain; charset=utf-8",
      ...headers,
    },
  });
}

function allowed_origin(env) {
  return env?.ALLOWED_ORIGIN === PRODUCTION_ORIGIN ? PRODUCTION_ORIGIN : null;
}

function asset_path(request) {
  const url = new URL(request.url);
  if (url.search || url.hash) {
    return null;
  }

  const match = ASSET_PATH.exec(url.pathname);
  if (!match) {
    return null;
  }

  return {
    key: `${match[1]}.${match[2]}`,
    sha256: match[1],
    mime_type: MIME_TYPES[match[2]],
  };
}

function valid_etag(value) {
  return typeof value === "string" && /^"[!#-~]+"$/.test(value);
}

function is_valid_asset(object, asset) {
  return (
    object &&
    Number.isSafeInteger(object.size) &&
    object.size > 0 &&
    object.size <= MAX_ASSET_BYTES &&
    object.httpMetadata?.contentType === asset.mime_type &&
    object.httpMetadata?.cacheControl === CACHE_CONTROL &&
    object.customMetadata?.sha256 === asset.sha256 &&
    valid_etag(object.httpEtag)
  );
}

function asset_headers(object, env) {
  const headers = new Headers({
    "Cache-Control": CACHE_CONTROL,
    "Content-Length": String(object.size),
    "Content-Type": object.httpMetadata.contentType,
    ETag: object.httpEtag,
  });
  const origin = allowed_origin(env);
  if (origin) {
    headers.set("Access-Control-Allow-Origin", origin);
  }
  return headers;
}

function preflight(request, env) {
  const origin = allowed_origin(env);
  if (!origin || request.headers.get("Origin") !== origin) {
    return no_store(403, "Origin not allowed");
  }

  return new Response(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Methods": "GET, HEAD",
      "Access-Control-Allow-Origin": origin,
      "Cache-Control": "no-store",
    },
  });
}

export async function handle_request(request, env) {
  const asset = asset_path(request);
  if (!asset) {
    return no_store(400, "Invalid asset path");
  }

  if (!ALLOWED_METHODS.split(", ").includes(request.method)) {
    return no_store(405, "Method not allowed", { Allow: ALLOWED_METHODS });
  }

  if (request.method === "OPTIONS") {
    return preflight(request, env);
  }

  let object;
  try {
    const operation = request.method === "HEAD" ? "head" : "get";
    object = await env?.CATALOG_ASSETS?.[operation](asset.key);
  } catch {
    return no_store(502, "Asset unavailable");
  }

  if (!object) {
    return no_store(404, "Asset not found");
  }
  if (!is_valid_asset(object, asset)) {
    return no_store(502, "Asset unavailable");
  }

  return new Response(request.method === "HEAD" ? null : object.body, {
    status: 200,
    headers: asset_headers(object, env),
  });
}

export default {
  fetch: handle_request,
};
