# Deployment — Alibaba Cloud

> **Status: built and run locally; not yet deployed to ECS.** `docker compose build` and
> `up` were verified on the development machine (Docker 29.9, Compose 5.6): the API image's
> test stage passes inside the build, the API reports healthy before the web container
> starts, the API runs as a non-root user, both ports bind to localhost only, and a full
> demo — verdict, live Qwen explanation, correction logged to the `feedback` volume — works
> through the web app's `/api` proxy. The ECS steps below have not been executed.

## What gets deployed

Two containers on one private network. Only the web app is ever exposed; the API is
reached through the web app's own `/api` route, so the browser sees a single origin and
there is no CORS to configure.

| | Image | Listens | Exposed |
|---|---|---|---|
| API | `Dockerfile` (repo root) — FastAPI + the committed models, non-root | 8000 | `127.0.0.1` only |
| Web | `frontend/Dockerfile` — Next.js standalone, non-root | 3000 | `127.0.0.1`, behind nginx |

The API image ships the committed artefacts in `models/` — it does not download Synthea
or retrain at build time, so what is deployed is exactly what was evaluated. The test
suite runs as a build stage; a failing test fails the build.

**Before anything else:** confirm the current submission requirements from the official
hackathon rulebook. Whether a public URL is mandatory decides how much of this you need.

## Local check first

```bash
cp .env.example .env              # fill in the DashScope values
docker compose --env-file .env up --build
```

<http://localhost:3000> should behave exactly as `npm run dev` does. The correction log
lives on the `feedback` volume and survives `docker compose down` / `up`.

## ECS

A single small instance is enough now that nothing trains at build time:
`ecs.e-c1m1.large` (2 vCPU, 2 GB) is comfortable.

```bash
# 1. Instance: Ubuntu 22.04. Security group: 22 from your IP only; 80 and 443 public.
ssh root@<elastic-ip>

# 2. Docker and nginx
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
apt-get install -y nginx certbot python3-certbot-nginx

# 3. Code
git clone https://github.com/mirza-ahsan/tasdeeq-e-bima.git
cd tasdeeq-e-bima

# 4. Secrets — create .env ON THE SERVER. Never commit it, never bake it into an image.
cat > .env <<'EOF'
DASHSCOPE_API_KEY=...
DASHSCOPE_BASE_URL=https://ws-<workspace>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
DASHSCOPE_WORKSPACE_ID=ws-<workspace>
QWEN_MODEL=qwen-plus
QWEN_VL_MODEL=qwen3-vl-plus
EOF
chmod 600 .env

# 5. Build and run (both ports bind to 127.0.0.1; nginx is the only public door)
docker compose --env-file .env up -d --build
curl -s localhost:3000/api/health     # through the web app's proxy
```

Choose a region close to your DashScope endpoint (Singapore, for the workspace-scoped URL
in `.env.example`) so the Qwen round-trip is not noticeable on stage.

## Nginx and TLS

One origin, one upstream: the web app serves the page and proxies `/api` itself.

```nginx
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

server {
    listen 80;
    server_name claimcheck.example.com;
    # Document uploads: up to three 4 MB files, base64-encoded (nginx's default is 1 MB).
    client_max_body_size 20m;

    location /api/ {
        limit_req zone=api burst=20 nodelay;
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 60s;
    }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

`X-Forwarded-For` is *set* to the connecting address rather than appended to, so a
visitor cannot choose their own rate-limit key; compose tells the web app to trust it
(`TRUST_FORWARDED_FOR=true`) and the API to trust the web app (`TRUST_PROXY_HEADERS=true`),
and the API image runs uvicorn with `--no-proxy-headers` so nothing else is trusted. Then add
TLS:

```bash
certbot --nginx -d claimcheck.example.com    # rewrites the server block for 443 + redirect
```

The timeouts nest: Qwen gets 8 s plus one retry inside the API, the web proxy waits 30 s,
nginx 60 s — so a stalled Qwen call ends in the template fallback, never a 504.

## Abuse limits already in the app

- Starting a check is limited to 30 per minute per client (the API, keyed on the address
  the proxy forwards; `TRUST_PROXY_HEADERS=true` in compose).
- Sessions are capped at 500 and expire after 30 idle minutes.
- A result is computed once per state of a claim; viewing it again never calls Qwen.
- Qwen calls are capped at 30 per minute across all users; past that, the template is used.
- Document reading is capped at 6 per minute per client and 10 Qwen-VL calls per minute
  overall; uploads are limited to 3 files of 4 MB, are never written to disk, and only the
  extracted fields are kept, for 30 minutes.
- `/api/health` reports only whether models loaded and whether Qwen is configured — not
  the endpoint, workspace or session count.

## Before real patient data

This deployment is for synthetic data. Before a partner clinic's real claims touch it:

- **Feedback log.** It stores each corrected claim's answers. It needs encryption at rest,
  access control, and a retention period (for example 90 days, then delete). Mount the
  volume on encrypted storage.
- **Third-party processing.** Field labels and values (procedure type, amount, policy
  status) are sent to Alibaba Cloud DashScope to be worded. That needs a data-processing
  agreement, a deliberate region choice, and a line in the clinic's patient notice — or
  `DASHSCOPE_API_KEY` left unset, which keeps everything on the server via the template.
- **Auth.** There is none. Put the app behind the clinic's login or an authenticating
  proxy before it holds anything real.

## Things that will bite

- **First build downloads fonts and npm packages.** The build machine needs internet.
- **`.env` on the server only.** `.gitignore` and both `.dockerignore` files exclude it.
- **Sessions are in memory.** Run one API process (the default); more than one worker
  would split sessions between them.
- **Moving the API** is a change to `API_URL` and a restart of `web`, not a rebuild.

## Rollback

```bash
docker compose down
git checkout <last-good-sha>
docker compose --env-file .env up -d --build
```
