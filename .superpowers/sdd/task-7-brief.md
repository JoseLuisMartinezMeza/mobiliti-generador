### Task 7: Security, production rollout, and project record

**Files:**
- Modify: `armado-caratula/32-Cambio-catalogo-offiho-carrito-cotizacion.md` through Obsidian MCP only.

**Interfaces:**
- Consumes all local deliverables.
- Produces a Supabase migration, Vercel deployment, Hetzner worker deployment, and production E2E evidence.

- [ ] **Step 1: Run final repository checks**

Run:

```powershell
git diff --check
python -m pytest tests\test_offiho_catalog.py tests\test_tarkett_catalog.py tests\test_quote_jobs_api.py tests\test_quote_worker.py tests\test_web_ui_defaults.py tests\test_quote_engine_image_layout.py -q
Set-Location mobiliti_saas\web
npm.cmd run build
```

Scan added lines for token, password, JWT, private key, signed URL, and service-key patterns. Expected: no real secrets.

- [ ] **Step 2: Apply the Supabase migration**

Apply `2026_07_offiho_reservations.sql` to project `hcdspekajlszcycecpml`, then query `information_schema.tables` and indexes to confirm the table and three indexes exist.

- [ ] **Step 3: Deploy web/API to Vercel production**

Deploy from `mobiliti_saas/web`, confirm `Ready`, assign the stable alias `https://web-lemon-one-45.vercel.app`, and verify `/health` reports `storage_provider=r2` and `storage_configured=true`.

- [ ] **Step 4: Deploy the worker**

Push the current branch, deploy it to `/opt/mobiliti-worker/app` on Hetzner through the existing deployment command, rebuild/restart `mobiliti-worker`, and verify the container is healthy at the deployed commit.

- [ ] **Step 5: Execute production smoke tests**

Authenticate without logging credentials or tokens. Verify `GET /offiho/catalog` returns 1,206 unique visible products and source audit metadata `1286/80/1206`, create an available and an exhausted quote, observe `queued -> processing -> completed`, download both XLSX files, verify required sheets and warning text, then create one Tarkett smoke quote to confirm regression safety.

- [ ] **Step 6: Update Obsidian and final status**

Append implementation files, catalog coverage, test counts, migration name, branch/commit, deployment result, job ids, and production verification to `armado-caratula/32-Cambio-catalogo-offiho-carrito-cotizacion.md`. Set `status=produccion_validada` and `production_verified=true`. Omit credentials, tokens, environment values, and signed URLs.

- [ ] **Step 7: Finalize the branch**

Commit any remaining scoped documentation/test evidence, push `codex/offiho-catalog-20260709`, and report exact verification results and any unmatched product coverage without claiming unsupported completeness.
