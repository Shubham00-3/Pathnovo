# Deployment

The take-home does not require a hosted URL — `docker compose up --build` already
satisfies "one documented command". This exists because a live demo link is
convenient, not because anything depends on it.

## What you are deploying

A single container: FastAPI serving the built React UI and the `/api/*` routes.
No database, no external services, no credentials.

**There is no upload endpoint.** Every route operates on PIDs resolved from
`data/registry.json`, and the image ships only the synthetic sample pairs. A
visitor can run the bundled comparisons and chat over them; they cannot submit
documents. That makes a public deploy low-risk, and it is the reason the
recommendations below are comfortable with an unauthenticated demo.

Private drawings are excluded three times over — `.gitignore`, `.dockerignore`,
and an explicit `rm -rf data/private_inputs` in the image build.

## Resource requirements

| Resource | Need | Why |
|---|---|---|
| RAM | **2 GB minimum**, 4 GB comfortable | onnxruntime + OpenCV + SciPy/sklearn resident; page rasters are transient but large |
| Disk | ~3 GB image | Python scientific stack dominates; ONNX weights are ~15 MB |
| CPU | 1 vCPU works | OCR is ~2.6s/page single-threaded |
| Cold start | 30–60s | Image size, not app init — models are pre-warmed at build |

**A 512 MB free tier will OOM.** That rules out Render's free web service, which
is the most common first attempt.

## Recommended: Hugging Face Spaces

Free, no credit card, 16 GB RAM, persistent public URL, and it takes a Dockerfile
directly. The image is already compatible — it runs as uid 1000 and honours
`PORT`.

1. Create a Space at <https://huggingface.co/new-space> → SDK **Docker** → Blank.
2. Add this frontmatter to the top of the Space's `README.md`:

   ```yaml
   ---
   title: Document Delta and Grounded Chat
   emoji: 📐
   colorFrom: blue
   colorTo: gray
   sdk: docker
   app_port: 7860
   pinned: false
   ---
   ```

3. Push this repo to the Space remote:

   ```bash
   git remote add space https://huggingface.co/spaces/<user>/<space-name>
   git push space main
   ```

4. Set `PORT=7860` in the Space's **Settings → Variables** (or rely on
   `app_port` above and leave the container default).

First build takes ~10 minutes. Subsequent pushes are faster.

## Alternative: Google Cloud Run

Better if you want scale-to-zero and a custom domain. Needs a billing account,
though this workload stays inside the free tier.

```bash
gcloud run deploy delta-chat \
  --source . \
  --region us-central1 \
  --memory 4Gi \
  --cpu 2 \
  --timeout 300 \
  --allow-unauthenticated
```

Cloud Run injects `PORT`; the container already honours it. Raise `--timeout`
above the default if you plan to run the mismatch pair, whose delta is large.

## Alternative: Fly.io

Also fine, and closest to a normal VM. Requires a card on file even for the free
allowance.

```bash
fly launch --no-deploy
fly scale memory 2048
fly deploy
```

Set `PORT = 8080` in `fly.toml`'s `[env]` to match Fly's internal port
convention.

## Local

```bash
docker compose up --build      # http://localhost:8000
```

Compose binds to `127.0.0.1` deliberately, so the local demo is not exposed on
your network.

## Before you make it public

- **No authentication exists.** This is fine given there is no upload path and
  no data beyond synthetic samples, but do not add an upload endpoint and leave
  it open.
- **`CAPTURE_LLM_CONTENT` must stay `false`.** It writes prompts, responses and
  raw questions to `artifacts/` in plaintext.
- **Do not mount `data/private_inputs/`** into a hosted container. The supplied
  P&IDs are not yours to redistribute.
- `artifacts/` grows per run. Nothing prunes it; on a long-lived deploy either
  mount a volume with a retention policy or accept it as ephemeral.
