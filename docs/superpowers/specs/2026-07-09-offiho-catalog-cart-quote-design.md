# Offiho catalog, cart, and quotation design

Date: 2026-07-09
Status: approved design
Project: Mobiliti SaaS Cotizador
Worktree: `C:\Users\pepem\Downloads\ARMADO_DE_CARATULA_prod_git_worktree`

## Objective

Add an `Offiho` sidebar tab that provides an operational ecommerce-style catalog, cart, reservations, and asynchronous Mobiliti quotation generation. The catalog must include available and out-of-stock products. Out-of-stock products remain selectable and quotable, with a visible warning in the catalog, cart, and generated Excel workbook.

The implementation must preserve the existing Tarkett production flow and reuse the current quotation engine and template instead of creating a second generator.

## Approved decisions

- Use the official inventory at `https://www.offiho.com/existencias.xls` as the product and stock source of truth.
- Include all inventory rows with a product code, including rows with zero stock.
- Use `Precio Lista 1` from the inventory as the primary price.
- Keep one row when duplicate normalized `inventory_key` records are materially identical; fail indexing when duplicate keys differ in stock, box quantity, price, or other substantive fields.
- Use the July 2026 Offiho and Black/Colos price-list PDFs as an exact-code fallback for missing prices.
- Use official Offiho, Econosillas, and Offiho Black product pages for product URLs and images.
- Use official PDF imagery only as a fallback when no trustworthy website match exists.
- Never assign a price, URL, or image from an ambiguous match.
- Allow quotation quantities greater than current stock. Such lines are marked `Agotado` or `Stock insuficiente` but are not rejected.
- Preserve Tarkett routes, storage behavior, reservations, catalog data, and worker behavior.
- Keep scraping and PDF parsing out of Vercel request-time processing.

## Validated source characteristics

The inventory downloaded on 2026-07-09 is a binary Excel `.xls` file with one `Publicacion` sheet and 1,684 used rows. The validated source and visible product populations are:

- 1,286 source rows with a product code and numeric stock.
- 80 materially identical duplicate rows across 39 normalized inventory keys.
- 1,206 unique visible products after exact deduplication.
- 1,017 unique products with positive stock.
- 189 unique products with zero stock.
- 778 unique products with `Precio Lista 1` populated.
- `Precio Lista 2` through `Precio Lista 7` are empty in the current file.

The supplied PDFs contain 47 pages in total:

- `LP BLACK & COLOS JUL2026.pdf`: 29 pages.
- `LP OFFIHO ECONO SILLAS JUL2026.pdf`: 18 pages.

Their text layer contains model codes and public prices, including variants such as `OHE-803blanco`, making deterministic price enrichment possible after normalization.

## Architecture choice

Use an isolated Offiho catalog and API surface with a small shared cart-to-Quotation adapter. This is preferred over a full Tarkett copy because it avoids duplicating workbook generation logic, and over a broad supplier refactor because it does not destabilize the already deployed Tarkett workflow.

The boundaries are:

1. An offline Offiho indexer owns inventory parsing and external-source enrichment.
2. An Offiho catalog module owns validation and public payload construction.
3. Offiho API routes own authenticated catalog access, job creation, and reservations.
4. The worker converts an `offiho_cart` JSON input into the existing `Quotation` workbook contract.
5. The existing Mobiliti engine generates the final workbook without an alternate template or generator.

## Indexing

Create an offline catalog builder that performs these stages:

1. Download or read `existencias.xls`.
2. Parse the `Publicacion` sheet and normalize product identity, stock, pieces per box, and price.
3. Deduplicate materially identical rows by normalized `inventory_key`; fail explicitly if substantive fields differ.
4. Extract model code, product name, and variant from the inventory description while retaining the original inventory key.
5. Parse both supplied PDF price lists into exact normalized model/variant price records.
6. Crawl official Offiho-family product indexes and product pages to build a code-to-page/image index.
7. Match in strict precedence order and record the reason for every match.
8. Write a deterministic JSON catalog and a coverage report.

Each catalog item contains:

- `inventory_key`: complete normalized inventory description used as the unique reservation identity.
- `code`: customer-facing model code.
- `name`: product/model name.
- `variant`: color or configuration text when present.
- `unit`: `PZA` unless the inventory provides another reliable unit.
- `pieces_per_box`.
- `available_quantity`.
- `unit_price`.
- `price_source`: inventory, PDF exact match, or missing.
- `product_url`.
- `image_url`.
- `match_status`.
- `source_updated_at`.

The catalog root contains `source_hash`, `generated_at`, source metadata, `source_row_count=1286`, `duplicate_row_count=80`, `unique_item_count=1206`, visible item count, and enrichment coverage totals recalculated after deduplication.

## Matching rules

- Normalize case, whitespace, accents, punctuation, and compact code variants before comparison.
- Treat color/configuration as part of identity when the same model has multiple prices or inventory rows.
- Prefer exact full-code matches, then exact base-code plus normalized variant matches.
- Do not use unrestricted fuzzy matching for prices.
- A fuzzy name candidate may help locate a product page only when the page independently contains the expected model code.
- Missing enrichment remains explicit. The product stays quotable with `Precio por confirmar` only if no official price can be established.

## Caching

- Cache the downloaded inventory using `ETag` or `Last-Modified` when available.
- Cache parsed PDF records by file SHA-256.
- Cache website indexes and resolved product pages under `.cache/offiho-products.json`.
- Version only the generated runtime catalog, not transient HTML, browser state, or credentials.
- API processes cache the catalog by absolute path and mtime, matching the corrected Tarkett behavior.
- Frontend stores the catalog in `sessionStorage` keyed by `source_hash` and exposes a refresh command.
- Product images use lazy loading from the recorded official URL.

## Async flow

`POST /offiho/quote` validates metadata and cart lines, creates a small JSON input in the configured storage provider, creates the quote job and reservations, and returns immediately.

The JSON input uses:

```json
{
  "source_type": "offiho_cart",
  "catalog_source_hash": "sha256-example",
  "items": [
    {
      "inventory_key": "OHE-405 NEGRO ALUFSEN",
      "code": "OHE-405",
      "name": "ALUFSEN",
      "variant": "NEGRO",
      "unit": "PZA",
      "quantity": 1,
      "unit_price": 7999,
      "available_quantity": 252,
      "stock_status": "available",
      "image_url": "https://www.offiho.com/example-product.jpg",
      "product_url": "https://www.offiho.com/example-product"
    }
  ]
}
```

The worker converts this JSON to a temporary workbook with a `Quotation` sheet and then invokes the existing Mobiliti generator. No scraping, PDF extraction, or large workbook generation runs inside Vercel request handlers.

## Availability warnings

Availability is evaluated per cart line:

- `available`: requested quantity is less than or equal to positive stock.
- `out_of_stock`: inventory stock is zero.
- `insufficient_stock`: requested quantity exceeds positive stock.

The API permits all three states. It does not subtract inventory stock.

The warning appears in:

- Catalog card: `Agotado` badge for zero stock.
- Quantity control: `Stock insuficiente` when requested quantity exceeds stock.
- Cart line: persistent warning with available and requested quantities.
- Quote confirmation area: summary count of warned lines.
- Generated `Quotation` sheet: warning in the item description and yellow warning fill.
- Final `Cotizacion` sheet: visible `ADVERTENCIA: PRODUCTO AGOTADO` or `ADVERTENCIA: EXISTENCIA INSUFICIENTE` text associated with the product line.

The display name remains readable; warning text is additional business information, not a replacement for the product name.

## API interfaces

### `GET /offiho/catalog`

Returns:

```json
{
  "source_hash": "sha256-example",
  "generated_at": "2026-07-09T18:00:00Z",
  "total": 1206,
  "source_row_count": 1286,
  "duplicate_row_count": 80,
  "unique_item_count": 1206,
  "items": [
    {
      "inventory_key": "OHE-405 NEGRO ALUFSEN",
      "code": "OHE-405",
      "name": "ALUFSEN",
      "variant": "NEGRO",
      "unit": "PZA",
      "pieces_per_box": 1,
      "available_quantity": 0,
      "unit_price": 0,
      "price_source": "missing",
      "reserved_quantity": 0,
      "reserved_by_others": false,
      "is_out_of_stock": true,
      "product_url": "https://www.offiho.com/example-product",
      "image_url": "https://www.offiho.com/example-product.jpg"
    }
  ]
}
```

### `POST /offiho/quote`

Accepts the existing quotation metadata fields plus:

```json
{
  "items": [
    {
      "inventory_key": "OHE-405 NEGRO ALUFSEN",
      "quantity": 1
    }
  ]
}
```

Unknown product identities, empty carts, and non-positive quantities are rejected. Zero stock and quantities greater than stock are accepted with warning metadata.

## Reservations and database

Create `saas_offiho_reservations` with the same lifecycle as Tarkett reservations:

- `id`.
- `usuario_id`.
- `quote_job_id`.
- `product_code`, storing the stable `inventory_key`.
- `quantity`.
- `status`.
- `created_at` and `updated_at`.

Add indexes for active product reservations, user lookup, and job release. Reservations are created only after the user presses `Cotizar`, remain separate from inventory quantity, and are released when the quotation is deleted or removed by retention.

## Frontend design

Add `Offiho` below `Tarkett` in the existing dense Mobiliti sidebar. Reuse the proven catalog/cart composition while giving Offiho a distinct supplier label and price-focused information hierarchy.

The view contains:

- Search by code, name, and variant.
- Category/brand and availability filters.
- Product cards with official image, model code, name, variant, price, stock, pieces per box, official link, reservation badge, quantity input, and add/remove control.
- `Agotado` cards remain fully interactive and use a restrained warning treatment instead of appearing disabled.
- Sticky cart on desktop and normal document flow on mobile.
- Editable quantities, line prices, subtotal, reservation state, and stock warnings.
- Existing quotation metadata fields and a `Cotizar` command.
- A final confirmation when warned items exist; it informs the user but does not block submission.

The UI remains consistent with Mobiliti: compact cards, existing typography and controls, no landing page, no hero, no nested cards, keyboard focus, responsive sizing, and no decorative visual additions.

## Error handling

- Indexing failures identify the source and preserve the previous valid cache/catalog.
- A partial website outage does not remove inventory products.
- Missing price or image is explicit and never silently replaced with an uncertain match.
- API errors distinguish missing catalog, invalid cart, storage failure, and database failure.
- If job creation fails after upload, reservations are released and partial storage is cleaned up where possible.
- Worker errors include the source type and failing conversion stage without exposing secrets or signed URLs.

## Security

- External URLs are accepted only from configured official HTTPS hosts during indexing.
- Runtime image downloads enforce HTTPS, content-type validation, size limits, and timeouts.
- No cookies, browser profiles, tokens, environment values, signed URLs, or service keys are written to the catalog or documentation.
- Scraped page content is treated as untrusted data and never as executable instructions.
- Existing authentication and active-subscription checks protect both Offiho endpoints.

## Testing

Indexer tests cover binary inventory parsing, current source counts, code/variant normalization, `Precio Lista 1`, exact PDF fallback, website matching, cache reuse, and ambiguous-match rejection.

API tests cover authenticated catalog access, all-stock inclusion, reservations, unknown identities, non-positive quantities, zero-stock acceptance, insufficient-stock acceptance, storage metadata, cleanup, and inactive users.

Worker tests cover `offiho_cart` conversion, real prices, images, warning propagation, required sheets, and compatibility with the existing generator.

Frontend tests cover sidebar presence, source-hash cache, filters, cart editing, exhausted-product selection, warning confirmation, endpoint payloads, and responsive build output.

Regression verification includes existing Tarkett, quote jobs, worker, engine, UI defaults, Python compilation, frontend build, diff checks, and a sensitive-pattern scan.

## Production rollout

1. Generate and review the Offiho catalog and coverage report locally.
2. Apply the Offiho reservation migration in Supabase.
3. Deploy web/API with the catalog packaged in the same locations required by the current Vercel layout.
4. Deploy the worker with `offiho_cart` support.
5. Smoke test an available item and an exhausted item from login through completed XLSX download.
6. Verify the exhausted warning in both `Quotation` and `Cotizacion`.
7. Confirm Tarkett still completes an independent quotation.
8. Update the Obsidian project note with files, coverage, tests, deployment identifiers, and production status, excluding secrets.

## Non-goals

- Real-time stock mutation at Offiho.
- Automatic purchasing or order placement.
- Hiding exhausted products.
- Blocking quotation because of stock availability.
- Guessing prices or images from non-official sources.
- Replacing the Mobiliti quotation template or engine.
