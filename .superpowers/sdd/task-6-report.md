# Task 6 Report: Offiho runtime package and local validation

Date: 2026-07-10
Branch: `codex/offiho-catalog-20260709`

## Delivered

- Packaged the quote-engine runtime for the Vercel web root, including the
  byte-identical catalog-cart modules, Offiho and Tarkett catalogs, their
  import dependencies, data, and engine assets. No `__pycache__` is packaged.
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

## Verification

- Focused suite:
  `python -m pytest tests/test_offiho_catalog.py tests/test_tarkett_catalog.py tests/test_quote_jobs_api.py tests/test_quote_worker.py tests/test_web_ui_defaults.py tests/test_quote_engine_image_layout.py -q`
  Result: `209 passed in 23.76s`.
- `py_compile` passed for the catalog builder, quote-engine modules, worker,
  deployable APIs, and packaged web runtime.
- `npm.cmd run build` passed.
- Root/web SHA-256 comparisons passed for `__init__.py`, `catalog_cart.py`,
  `offiho_catalog.py`, `tarkett_catalog.py`, and both runtime catalog JSON
  files. Importing from `mobiliti_saas/web` loaded total 1206, exhausted 189,
  and image coverage 135.
- Local DEV E2E used the supported worker script entrypoint and an isolated
  `.cache` store. Available, exhausted, and missing-price Offiho jobs all
  reached `completed`; each downloaded XLSX contained `Cotizacion`,
  `Mobiliti`, and `Quotation`. Exhausted and missing-price warnings were
  present in final workbooks, and no job remained queued.

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
