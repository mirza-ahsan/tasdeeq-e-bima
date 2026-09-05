# Deployment — Alibaba Cloud

> **Status: prepared, not executed.** The container definitions and the runbook below are
> written and the standalone Next.js build is verified, but Docker is not installed on the
> development machine, so **the images have not been built or run**. Budget an hour for the
> first build to shake out problems, and do it before demo day, not on it.

## What gets deployed

Two containers. The API trains its own models during the image build (about two minutes),
so no model artefacts need to be shipped or stored.

| | Image | Port |
|---|---|---|
| API | `Dockerfile` (repo root) — FastAPI + LightGBM + trained models | 8000 |
| Web | `frontend/Dockerfile` — Next.js standalone | 3000 |

**Before anything else:** confirm the current submission requirements from the official
hackathon rulebook. Whether a public URL is mandatory, and which Alibaba services qualify,
decides how much of the below you actually need.

## Local check first

```bash
docker compose --env-file .env up --build
```

`http://localhost:3000` should behave exactly as `npm run dev` does. If it does not, fix it
here rather than on the server.

Note `PUBLIC_API_URL`: Next.js inlines `NEXT_PUBLIC_*` **at build time**, so the browser-facing
API URL is baked into the image. Locally `http://localhost:8000` is right; on a server it must
be the address a visitor's browser can reach, not an internal one.

## ECS

A single instance is enough. `ecs.e-c1m1.large` (2 vCPU, 4 GB) or larger — LightGBM training
during the build wants the memory.

```bash
# 1. Instance: Ubuntu 22.04, security group open on 22, 80 (and 443 with a domain).
ssh root@<elastic-ip>

# 2. Docker
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

# 3. Code
git clone https://github.com/mirza-ahsan/tasdeeq-e-bima.git
cd tasdeeq-e-bima

# 4. Secrets — create .env ON THE SERVER. Never commit it, never bake it into an image.
cat > .env <<'EOF'
DASHSCOPE_API_KEY=...
DASHSCOPE_BASE_URL=https://ws-<workspace>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
DASHSCOPE_WORKSPACE_ID=ws-<workspace>
QWEN_MODEL=qwen-plus
PUBLIC_API_URL=http://<elastic-ip>:8000
EOF
chmod 600 .env

# 5. Build and run
docker compose --env-file .env up -d --build
docker compose logs -f api        # watch the training step complete
curl -s localhost:8000/api/health
```

Choosing a region close to your DashScope endpoint (Singapore, for the workspace-scoped URL
in `.env.example`) keeps the Qwen round-trip fast enough not to be noticeable on stage.

## Nginx

Serving both on port 80 avoids the mixed-port URL and is worth the ten minutes.

```nginx
server {
    listen 80;
    server_name _;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 60s;          # Qwen calls can take a few seconds
    }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

With this in place set `PUBLIC_API_URL=http://<elastic-ip>` (no port) and rebuild the web
image — the API URL is compiled in, so changing it means a rebuild, not a restart.

## Things that will bite

- **CORS.** `backend/main.py` allows only `localhost:3000`. Add the deployed origin, or
  serve both behind one nginx origin as above, in which case no change is needed.
- **`NEXT_PUBLIC_API_URL` is build-time.** Changing it requires `--build`.
- **First build is slow.** It downloads Synthea and trains two models. Later builds reuse
  the layer unless `ml/` or `data/carc_codes.json` changed.
- **Memory.** Training inside the container is the peak. A 2 GB instance may OOM.
- **`.env` on the server only.** `.gitignore` and `.dockerignore` both exclude it; keep it
  that way and inject at runtime.
- **Feedback SQLite is inside the container.** It is wiped on redeploy. That is fine for a
  demo; mount a volume if you want the log to survive.

## Rollback

```bash
docker compose down
git checkout <last-good-sha>
docker compose --env-file .env up -d --build
```
