# Task 5 Report: Offiho catalog and cart tab

## Scope

- Added the active-user `Offiho` sidebar tab directly below `Tarkett`.
- Added `OffihoView` for `/offiho/catalog` and `/offiho/quote` with cache recovery, search, compact filters, stock and reservation warnings, cart totals, and required quotation metadata.
- Added the `/offiho/:path*` Vercel rewrite and focused Offiho CSS modifiers.

## TDD

- RED: added `test_offiho_tab_catalog_cart_and_warning_contracts_are_present` and ran `python -m pytest tests/test_web_ui_defaults.py -q`.
- RED result: failed because `Armchair` and the Offiho frontend contract were absent.
- GREEN: implemented the tab, view, cache, cart controls, warnings, confirmation, rewrite, and styles.
- GREEN result: `7 passed in 0.06s`.

## Verification

- `python -m pytest tests/test_web_ui_defaults.py -q` passed.
- `npm.cmd run build` passed.
- `git diff --check` passed.
- Parsed `mobiliti_saas/web/vercel.json` successfully with PowerShell `ConvertFrom-Json`.

## Follow-up Corrections

- Consolidated the pre-existing Tarkett frontend so a clean checkout exposes Tarkett immediately before Offiho.
- Replaced unsupported brand/category filters with factual unit and availability filters.
- Added user-scoped catalog caches, cart reconciliation notices, missing-price treatment, quantity draft validation, an in-flight submit ref, and 24-item Offiho pagination.
- Visual QA used a mocked 1,206-item catalog: 24 rendered product cards on each page, no horizontal overflow, and document heights of 2,778px at 1440x900, 3,804px at 912x791, and 5,515px at 390x844.

## Final Layout Corrections

- RED: added `test_catalog_shell_is_unframed_and_intermediate_breakpoint_prevents_overlap`; it failed while both views still used the framed `main-card full` shell.
- GREEN: removed the outer card treatment and added the 1,390px intermediate breakpoint so toolbars and product grids use one column while the cart remains lateral through 1,121px.
- Added `min-width: 0` containment for the layout, product cards, and product actions; indicators align left at the intermediate breakpoint.
- Playwright clicked the second product card and measured 24 products, zero product/cart intersections, an 18px gap, no horizontal overflow, and a 5,114px document height at 1,366px, 1,280px, 1,200px, and 1,121px widths.
- Computed shell styles at every measured width were transparent background, zero border width/radius, and no box shadow.
- No API, catalog worker, or XLSX template changes were included.

## Files

- `mobiliti_saas/web/src/main.jsx`
- `mobiliti_saas/web/src/styles.css`
- `mobiliti_saas/web/vercel.json`
- `tests/test_web_ui_defaults.py`
