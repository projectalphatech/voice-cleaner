# Deploy the voice cleaner web app

Two parts:
- **VPS:** `scripts/server.py` runs the chain. It is a FastAPI job API on `127.0.0.1:8090` behind a bearer token.
- **Cloudflare Worker:** `worker/` serves the upload page and proxies `/api/jobs`, `/api/jobs/<id>` and `/api/jobs/<id>/result` to the VPS, adding the token. Every other path returns 404.

## VPS

```bash
sudo apt install ffmpeg python3-venv
cd voice-cleaner/scripts
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python clean.py --selfcheck
AUDIO_TOKEN=<long random secret> AUDIO_DATA=/var/lib/voice-cleaner .venv/bin/uvicorn server:app --host 127.0.0.1 --port 8090
```

Expose it over HTTPS, for example with a Cloudflare Tunnel to `127.0.0.1:8090`. Keep the token only in the VPS `.env`, never in git.

API (all routes except `/health` need `Authorization: Bearer <AUDIO_TOKEN>`):

| Method | Path | Body / result |
|---|---|---|
| POST | `/jobs` | multipart: `file`, `target` (LUFS), `denoise`, `leveler`, `fmt` → `{id}` |
| GET | `/jobs/{id}` | `{status: queued, processing, done or error, error, info}` |
| GET | `/jobs/{id}/result` | the cleaned file |
| GET | `/health` | `{ok, queued}` |

## Worker

```bash
cd voice-cleaner/worker
# set VPS_URL in wrangler.jsonc to the tunnel's HTTPS address
npx wrangler secret put VPS_TOKEN   # same value as AUDIO_TOKEN
npx wrangler deploy
```

Put Cloudflare Access in front of the Worker before sharing it. Otherwise anyone with the URL can use the VPS CPU.

`wrangler dev` crashes on the founder's Windows PC (`SQLITE_CANTOPEN` from workerd). Test the Worker on the VPS or in CI instead.

## Limits

- Worker request bodies cap at 100 MB on the Free and Pro plans. The API itself accepts up to 500 MB.
- Job status lives in memory and is lost on restart. Job files are never deleted, so add a cron cleanup.
- One job runs at a time.
