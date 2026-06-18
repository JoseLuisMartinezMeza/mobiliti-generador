# Mobiliti Web Cotizador

App React/Vite para crear cotizaciones desde navegador.

Deploy Vercel root: este folder (`mobiliti_saas/web`). No usar
`mobiliti_saas` root.

Variables:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_SUPABASE_URL=https://[YOUR_PROJECT_REF].supabase.co
VITE_SUPABASE_ANON_KEY=[YOUR_SUPABASE_ANON_KEY]
```

Comandos:

```powershell
npm install
npm run dev
npm run build
```

La web usa `uploadToSignedUrl`, por eso necesita la anon key publica de Supabase. La service role key queda solo en backend/worker.

En produccion `VITE_API_BASE_URL` debe apuntar al deployment de
`vercel_deploy`, no a la API legacy dentro de `mobiliti_saas/api`.
