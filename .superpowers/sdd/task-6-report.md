# Task 6 Report: Offiho runtime package and local validation

Date: 2026-07-10
Branch: `codex/offiho-catalog-20260709`

## Delivered

- Packaged only the catalog runtime required by the Vercel API: byte-identical
  `catalog_cart.py`, `offiho_catalog.py`, `tarkett_catalog.py`, and the Offiho
  and Tarkett catalog JSON files. The worker continues to use the root engine;
  no engine, image-provider dependencies, assets, or `__pycache__` are packaged.
- Regenerated the versioned Offiho catalog from the supplied local inventory,
  PDFs, and an official-host bounded live cache. The resulting audit is
  `1286 source rows - 80 exact duplicates = 1206 unique products`, with 189
  exhausted products and 135 official, HTTPS, MIME/size-verified product
  images.
- Hardened image selection with TDD for Offiho CIAO and Shopify pages. Branding,
  menu, social, price-list, warranty, box, and accessory candidates are
  excluded; product/model paths, codes, and front/principal candidates rank
  first. Shopify gallery links and `srcset` candidates are supported without
  treating CDN assets as crawl pages.
- Propagated `price_source` into the Offiho cart payload. Missing source prices
  produce `ADVERTENCIA: PRECIO POR CONFIRMAR` with yellow fill in `Quotation`;
  the final `Cotizacion` preserves that warning and the price remains zero.
- Versioned the supplied Offiho inventory source, Tarkett inventory, and both
  Offiho PDFs. Transient `.cache`, DEV store, and generated E2E workbooks are
  excluded.

## TDD evidence

- RED: CIAO image ranking, Shopify `srcset`/gallery selection, CDN placeholder
  rejection, crawl URL normalization, and price-pending workbook propagation
  failed against the prior behavior.
- GREEN: the nine focused regressions passed after the minimal implementation
  changes.
- Review RED: the final warning description retained `fill_type=None`; the
  first GREEN then exposed that copying row 17 propagated yellow to a normal
  product row.
- Review GREEN: the engine snapshots the base description fill before writing
  products, restores it per product, and applies shared `FFF2CC` only when the
  final visible description contains `ADVERTENCIA:`. One final-workbook test
  covers exhausted, missing-price, both-warning, normal, and category rows.

## Verification

- Focused suite:
  `python -m pytest tests/test_offiho_catalog.py tests/test_tarkett_catalog.py tests/test_quote_jobs_api.py tests/test_quote_worker.py tests/test_web_ui_defaults.py tests/test_quote_engine_image_layout.py -q`
  Review result: `210 passed in 25.12s`.
- `py_compile` passed for the catalog builder, quote-engine modules, worker,
  deployable APIs, and packaged web runtime.
- `npm.cmd run build` passed.
- Root/web SHA-256 comparisons passed for `catalog_cart.py`,
  `offiho_catalog.py`, `tarkett_catalog.py`, and both runtime catalog JSON
  files. Importing from `mobiliti_saas/web` loaded total 1206, exhausted 189,
  and image coverage 135.
- Local DEV E2E used the supported worker script entrypoint and an isolated
  `.cache` store. Each job transitioned `queued -> completed`; each output
  contained `Cotizacion`, `Mobiliti`, and `Quotation`. The exhausted and
  missing-price outputs preserved warning text and solid `FFF2CC` fill in both
  temporary `Quotation` and final `Cotizacion`. The available output preserved
  a real positive price with no warning. No job remained queued.

## Review E2E evidence

| Case | Job ID | Output SHA-256 | Bytes | Assertions |
| --- | --- | --- | ---: | --- |
| Available | `75ef3db5-c6c8-41f0-bb2b-738537e4122c` | `0ee1db3bedffb6949475b2b543811d8f6c32e8e0afd00f6a1f8439fcb1972816` | 22490368 | `queued -> completed`; required sheets; positive real price; no warning |
| Exhausted | `8e7df54b-fac1-46d1-861f-6e9e56389966` | `578d1fe25d767800d8b417df524c73c4ae6363ca62b86a33c1da56363f38f04b` | 22490444 | `queued -> completed`; required sheets; stock warning text and solid `FFF2CC` in temporary/final |
| Missing price | `18bd33bb-bdd6-4bcf-bf96-9965c3803bcf` | `59628f6e7796383e327d7ff627c67e4aa1bb280d84ea57a286818fa61aa40bc9` | 22490421 | `queued -> completed`; required sheets; price warning text and solid `FFF2CC` in temporary/final |

Final DEV queue count: `0`.

## Catalog coverage

- Official verified images: 135 product lines, 23 distinct product image URLs.
- Image sample validation: no branding/utility tokens and every selected URL
  is distinct from its product page URL and present as verified in the live
  official cache.
- The live catalog does not claim the previous approximate 191-image coverage;
  unmatched lines remain explicitly image-less rather than using a generic or
  branded image.

## Source hashes

- `catalog_sources/offiho/existencias.xls`:
  `F6F2025BB6AE25AFABA4C24A0F1877C2D6B577D6827EF9DA12967B039371DCFF`
- `Inventario Tarkett- 6 Julio .xls`:
  `05971B64C74224DBCD62100F4BB91F690F8BC4DE47E234150A7A6C9002D6FDBD`
- `LP BLACK & COLOS JUL2026.pdf`:
  `2A8122D4484AC4C851EF906B1D5748BBFB3C24BA975C41A2D33ABB5DA74F281B`
- `LP OFFIHO ECONO SILLAS JUL2026.pdf`:
  `D36E291DDE6A2D19D9D03994F993804D46A62ADEF6B9BE916A3DA7A42E8D8F15`

## Deployment

No Supabase, Vercel, or Hetzner deployment was performed.
