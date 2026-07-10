### Task 6: Package runtime data and verify locally

**Files:**
- Create: `mobiliti_saas/web/mobiliti_saas/quote_engine/catalog_cart.py`
- Create: `mobiliti_saas/web/mobiliti_saas/quote_engine/offiho_catalog.py`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/tarkett_catalog.py`
- Generate: `mobiliti_saas/web/mobiliti_saas/quote_engine/data/offiho_catalog.json`
- Test: `tests/test_quote_jobs_api.py`
- Test: `tests/test_web_ui_defaults.py`

**Interfaces:**
- Produces the package layout required by the Vercel web root.

- [ ] **Step 1: Copy runtime modules and catalog into the web package**

Copy the three quote-engine modules and Offiho JSON byte-for-byte from `mobiliti_saas/quote_engine`. Do not copy caches or source PDFs.

- [ ] **Step 2: Verify packaged equality and catalog load**

Compare SHA-256 hashes and run a Python import from the `mobiliti_saas/web` working directory. Expected visible catalog total is 1,206 with source audit metadata `1286/80/1206`, and both available and exhausted items load.

- [ ] **Step 3: Run the full focused suite**

```powershell
python -m py_compile scripts\build_offiho_catalog.py mobiliti_saas\quote_engine\catalog_cart.py mobiliti_saas\quote_engine\offiho_catalog.py mobiliti_saas\worker\quote_worker.py mobiliti_saas\web\api\index.py mobiliti_saas\api\index.py vercel_deploy\api\index.py
python -m pytest tests\test_offiho_catalog.py tests\test_tarkett_catalog.py tests\test_quote_jobs_api.py tests\test_quote_worker.py tests\test_web_ui_defaults.py -q
```

Expected: PASS.

- [ ] **Step 4: Run local E2E for available and exhausted products**

Start the local FastAPI dev backend, worker, and Vite app. Create one Offiho quote containing an available item and one containing an exhausted item. Verify both jobs reach `completed`; downloaded workbooks contain `Cotizacion`, `Mobiliti`, and `Quotation`; and the exhausted workbook contains the warning in both `Quotation` and `Cotizacion`.

- [ ] **Step 5: Commit packaged runtime files**

```powershell
git add mobiliti_saas/web/mobiliti_saas/quote_engine/catalog_cart.py mobiliti_saas/web/mobiliti_saas/quote_engine/offiho_catalog.py mobiliti_saas/web/mobiliti_saas/quote_engine/tarkett_catalog.py mobiliti_saas/web/mobiliti_saas/quote_engine/data/offiho_catalog.json
git commit -m "Package Offiho catalog for Vercel"
```

---
