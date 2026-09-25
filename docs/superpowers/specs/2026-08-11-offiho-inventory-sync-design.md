# Offiho inventory synchronization design

Date: 2026-08-11
Status: implemented and verified in the local worktree; production rollout pending authorization
Project: Mobiliti SaaS Cotizador

## Objective

Keep Offiho stock and product rows synchronized from the official
`https://www.offiho.com/existencias.xls` without rebuilding PDF/site enrichment in
Vercel requests. The existing quotation and reservation behavior remains unchanged.

## Considered approaches

1. Download and parse the XLS when any user clicks **Refrescar**. This is immediate,
   but couples a public request to an external site and makes Vercel the indexer.
2. Rebuild and redeploy the packaged JSON on a schedule. This preserves the offline
   builder, but every stock change requires a deployment and the button stays stale.
3. Reuse the worker and `saas_supplier_catalog_snapshots` used by Tarkett. The worker
   refreshes only inventory fields, publishes atomically, and the API retains its
   packaged fallback. This is the selected approach because it is durable, small, and
   keeps external parsing outside Vercel.

## Data flow

1. The idle worker downloads the official XLS on a bounded interval.
2. A shared runtime parser validates the workbook, deduplicates identical keys, and
   returns rows plus audit counts.
3. The updater takes the latest durable Offiho payload, or the packaged JSON on first
   run, and overlays current inventory identity, stock, box quantity, and price.
4. Exact keys preserve their existing official enrichment. A key differing only by a
   terminal `*` may inherit enrichment only when that normalized match is unique.
5. Rows absent from the current XLS disappear; new rows are added with explicit
   inventory-label descriptions and no guessed image or URL.
6. The updater publishes only when the inventory-derived `source_hash` changes.
7. The API reads the durable payload with a short TTL and falls back to the packaged
   JSON if persistence is unavailable or invalid.
8. `GET /offiho/catalog?fresh=1` bypasses the API TTL. The existing **Refrescar** button
   uses this route and browser `no-store`, so it obtains the most recently published
   snapshot; it does not execute an external download in the request.

## Validation and failure behavior

- Cardinality is dynamic; `unique_item_count` must equal the unique item list length,
  and `source_row_count` must equal unique plus duplicate rows.
- Keys must be non-empty and unique, numeric fields remain bounded, and item URLs stay
  on the existing official allowlist.
- A refresh outside a conservative item-count delta from the prior snapshot is rejected
  for review instead of replacing valid data.
- Network, MIME, size, parser, validation, or database failures leave the previous
  snapshot untouched.
- `generated_at` and `catalog_built_at` represent the successful inventory build;
  `inventory_last_modified` records the upstream HTTP timestamp when supplied.
- No PDF/site crawling, SharePoint write, or OpenAI dependency is added.

## Components

- `mobiliti_saas.quote_engine.offiho_inventory`: shared XLS parser, secure download, and
  stock-only catalog merger.
- `mobiliti_saas.quote_engine.offiho_catalog`: structural dynamic catalog validation.
- `mobiliti_saas.worker.quote_worker`: interval/force synchronization using the existing
  snapshot client.
- API mirrors: Offiho snapshot persistence, DB-preferred loading, internal worker routes,
  fresh-read query, and synchronization metadata.
- React Offiho view: fresh request semantics and real inventory timestamp.
- Supabase migration: extend the existing snapshot supplier check to `offiho`.

## Verification

- TDD RED/GREEN for dynamic validation, inventory merge, no-change hashing, failure
  preservation, worker publication, internal routes, API fresh-read, and browser refresh.
- Focused Offiho, worker, API, migration, and browser suites.
- Full relevant regression, Python compilation, Vite build, API mirror hash check.
- Read-only integration against the current official XLS proving 1,288 valid products and
  a loadable refreshed payload without writing production state.

## Rollout boundary

This task changes and verifies the local worktree only. Production requires, in order:
database migration, worker deployment, API/web deployment, live snapshot observation,
and authenticated smoke testing. Those external mutations require separate authorization.

## Local verification result

- Official XLS observed on 2026-08-11: SHA-256
  `3c8b6e5765b888c27607cc8247c5e5898b16b4a0193598e4623c9c547db95084`,
  297,984 bytes, `Last-Modified` 2026-08-11 14:46 UTC.
- The shared parser produced 1,368 source rows, 80 duplicates, and 1,288 unique
  products. The validated merge matched 1,204 of 1,207 packaged identities
  (99.75%), added 84, removed 3, and detected 649 stock changes.
- API plus migration regression: 331 passed, 1 opt-in PostgreSQL test skipped.
- Worker, dependency, and inventory regression: 165 passed.
- Browser refresh acceptance passed and the Vite production build completed.
