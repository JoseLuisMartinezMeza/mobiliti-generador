# Hetzner Worker Deploy

This directory deploys the Mobiliti quote worker on a Hetzner Ubuntu server.
The worker runs in pull mode: it polls Supabase for queued jobs, processes XLSX
files, and uploads results back to Supabase Storage. No public HTTP endpoint is
required.

## Server

Recommended server:

- Hetzner Cloud project: `mobiliti-worker-prod`
- Server type: `CCX13`
- OS: Ubuntu 24.04 LTS x86_64
- Region: `hil` if available, otherwise `ash`
- Inbound firewall: SSH only

## Bootstrap

### Option A: Provision from Windows with Hetzner API

From the repo root on Windows:

```powershell
$env:HCLOUD_TOKEN="paste_read_write_token_here"
$env:SUPABASE_ANON_KEY="paste_current_publishable_key_here"
$env:MOBILITI_REST_SECRET="paste_current_rest_secret_here"
powershell -ExecutionPolicy Bypass -File .\deploy\hetzner\provision.ps1
```

The script will:

- register the local SSH public key in the Hetzner project;
- create an SSH-only firewall;
- create/reuse `mobiliti-worker-prod-01`;
- install Docker/security packages/swap;
- upload `/etc/mobiliti-worker/worker.env`;
- build and start the worker.

### Option B: Bootstrap on an existing server

Run on the server as root:

```bash
curl -fsSL https://raw.githubusercontent.com/JoseLuisMartinezMeza/mobiliti-generador/master/deploy/hetzner/bootstrap.sh | bash
```

Then edit secrets:

```bash
nano /etc/mobiliti-worker/worker.env
```

Required values:

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `MOBILITI_REST_SECRET`
- `QUOTE_STORAGE_BUCKET=quote-files`

Do not commit real secrets.

## Deploy

```bash
mobiliti-worker-deploy
```

The health endpoint is bound to localhost only:

```bash
curl http://127.0.0.1:10000/health
```

Expected shape:

```json
{"status":"running","isolated_jobs":true,"ok":true}
```

## Operations

Logs:

```bash
docker logs -f mobiliti-worker
```

Restart:

```bash
docker restart mobiliti-worker
```

Update to latest `master`:

```bash
mobiliti-worker-deploy
```

Resource checks:

```bash
free -h
docker stats mobiliti-worker
df -h
```

## Cutover From Render

After Hetzner completes a real ESSENTIA job:

1. Suspend or delete Render service `mobiliti-quote-worker-web`.
2. Remove `WORKER_WAKE_URL` from Vercel Production.
3. Redeploy Vercel.
4. Confirm there are no `queued` or `processing` jobs stuck in Supabase.
