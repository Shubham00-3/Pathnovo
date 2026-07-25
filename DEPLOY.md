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

## Google Cloud Run (current live deployment)

Scale-to-zero, so an idle demo costs nothing. Needs a billing account.

```bash
gcloud run deploy pathnovo \
  --source . \
  --region asia-south1 \
  --memory 4Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 1 \
  --max-instances 1 \
  --min-instances 0 \
  --allow-unauthenticated \
  --set-env-vars LLM_PROVIDER=extractive,DELTA_CHAT_CONFIG=config/default.yaml
```

Cloud Run injects `PORT`; the container honours it. `--timeout 300` matters —
the mismatch pair produces a 624-change delta and will exceed the default.

**`--concurrency 1` and `--max-instances 1` are load-bearing, not tuning.** Run
artifacts are written to the container filesystem, so a chat request must reach
the same instance that produced the comparison it is asking about. Fan the
service out and chat will intermittently 404 against a run it cannot see. This
is the right trade for a single-reviewer demo and the wrong one for real use —
production would write artifacts to Cloud Storage and drop both limits. The same
ephemerality means run artifacts vanish when the instance is recycled; the
Evaluation tab falls back to the committed `eval/baseline.json` and labels it as
a baseline rather than a live run.

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

## Enabling a real LLM (optional)

The deployed demo runs `LLM_PROVIDER=extractive` — deterministic, no key, no
external calls, no bill. The LiteLLM path in `src/delta_chat/chat/llm.py` is
real but is not exercised by the default image, and the README says so rather
than implying a hosted model was evaluated.

To turn it on:

1. Build with the extra: `docker build --build-arg INSTALL_LLM=true .`
   The default is `false` because litellm is large and the default deployment
   never executes that path.
2. Set `LLM_PROVIDER` and `LLM_MODEL`.
3. Store the provider key as a **secret**, not a plain env var:

   ```bash
   gcloud run services update pathnovo \
     --update-secrets OPENAI_API_KEY=projects/PROJECT/secrets/openai-key:latest
   ```

4. Keep `extractive` as the fallback so the system still runs without a key.
5. Verify real token counts, latency and cost land in `llm_calls.jsonl`, and
   that citations still validate. Cost reports as `unavailable` until a provider
   returns a real figure — never `0.00`.

Never commit, echo, or log the key. Nothing in this repo requires one.

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
