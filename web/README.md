# Proteus web

- `static/` — the site (landing, lab, live run view). Deployed to GitHub Pages by
  `.github/workflows/pages.yml`; also served by the backend itself.
- `server.py` — the hosted lab backend: static + API, FIFO queue, bounded
  concurrency, BYO-key (memory only), hard caps, artifact TTL.

## Run the backend

```bash
python web/server.py --port 8400 --max-concurrent 2 --runs-dir /var/proteus-runs
```

Requirements on the box: Python 3.10+, git; Docker with the prepared images built
(`environments/deepseek-harness/`, `environments/pi/`) if you expose the dsh/pi
harnesses. The `minimal` and `llm` harnesses need no Docker.

The static pages auto-detect their backend: same origin when served by server.py;
on GitHub Pages they fall back to `http://localhost:8400`, and any other backend can be
set once per browser via `localStorage.setItem("proteus_api", "https://your-host")`.

## Deployment shape

GitHub Pages hosts the frontend. The backend runs wherever there is Docker — a small VPS
is enough. Put it behind a TLS reverse proxy (Caddy/nginx) and pass the public origin
through; the API is CORS-less by design (frontend and API share the origin in production;
the Pages frontend talking to a remote backend needs the proxy to add
`Access-Control-Allow-Origin` for the Pages origin).

Caps are constants at the top of `server.py` (arms/seeds/episodes/queue/TTL); concurrency
is `--max-concurrent`.
