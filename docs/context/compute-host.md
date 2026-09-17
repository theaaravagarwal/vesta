# Primary compute host

The primary host is `software@100.64.0.7` (Ubuntu 24.04, Python 3.12.3, RTX 4090 Laptop GPU, 16 GB VRAM, 32 GB RAM). The checkout lives at `/home/software/vesta`; no data or model files belong in Git. `docs/context/compute.md` is a pre-existing user document and is intentionally left unchanged.

## Installed environment

The checkout's `.venv` is synchronized from the repository lock with:

```bash
cd /home/software/vesta
~/.local/bin/uv sync --frozen --extra host --python /usr/bin/python3.12
```

Verified versions: uv 0.12.9, Flask 3.1.3, Gunicorn 23.0.0, PyTorch 2.10.0+cu128, Ultralytics 8.4.23, ONNX Runtime 1.24.4, OpenCV 4.13.0, Pillow 12.1.1. PyTorch reports CUDA available on the RTX 4090 Laptop GPU. ONNX Runtime has CPU/Azure providers only, so GPU detection uses the PyTorch checkpoint at `person-detect/yolo26s.pt`; the committed ONNX file is not the GPU inference path.

The checkpoint came from the official Ultralytics Assets v8.4.0 release. Its SHA-256 is `646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b`. It is ignored by Git.

User-local FFmpeg 9.0.1 and ffprobe binaries are under `.tools/ffmpeg-9.0/bin`, with symlinks in `.tools/bin`. The static archive is from the Linux x86-64 GPL release linked by ffmpeg.org to BtbN/FFmpeg-Builds. Archive SHA-256: `0248b5324cc9e0bb52e2a7a2e05667ad5b4eb94e0628af85c0e7e28992f2587d`. `deploy/vesta-web.service` places `.tools/bin` first in the service PATH.

Runtime folders for uploads, outputs, recordings, caches, and Ultralytics settings are under `/home/software/vesta/runtime`; `datasets/` is reserved for approved data. `YOLO_CONFIG_DIR` is set to the runtime settings folder to keep Ultralytics state out of the account-wide config.

## Inference services

`vesta-inference.service` binds Ollama to `127.0.0.1:8078` and stores models in `/home/software/vesta/models/ollama`. It uses the preinstalled Ollama 0.33.2 executable without changing that installation. The selected vision model is `qwen2.5vl:3b`, model digest `fb90415cde1ef08aa669ae74b082d49b158729b6db1ab183c941417d507e71a1` (3.20 GB). Its service context is 32,768 tokens: the exact 16-image OpenAI-compatible payload expands to roughly 17.5k prompt tokens and failed with an 8,192-token context.

`~/.config/vesta/host.env` is mode 600 and leaves the camera URL, discovery credentials, and camera context empty. It sets one YOLO frame worker, video batch size 4, and one LLM batch request to limit GPU contention. Do not put live camera credentials into tracked files.

The web unit runs `behavior:create_app()` as one Gunicorn process with eight threads on loopback port 33263. Access it through an SSH tunnel from the client:

```bash
ssh -N -L 33263:127.0.0.1:33263 software@100.64.0.7
```

Then open `http://127.0.0.1:33263/review` locally.

The planned private browser endpoint is Tailscale Serve HTTPS, but it is not
currently enabled. The custom control plane does not advertise certificate
domains, so plain HTTP Serve would not meet the browser secure-context
requirement for camera capture. The exact access boundary, HTTPS blocker, and
safe enable/rollback procedure are in
[`tailscale-access.md`](tailscale-access.md). Keep the Gunicorn listener on
loopback regardless of the chosen remote-access path.

## Verification and operation

Run the camera-free check from the checkout:

```bash
cd /home/software/vesta
set -a; . /home/software/.config/vesta/host.env; set +a
.venv/bin/python scripts/compute-check.py
```

The verified check ran PyTorch YOLO on one synthetic blank 640×640 frame on `cuda:0`, performed a synthetic linear-layer backward/optimizer step, then called the app's actual OpenAI-compatible client with synthetic 96×96 images in batches of 1 and 16. Both requests succeeded. The measured steady loaded GPU memory was about 5,989 MiB; warm request times were 2.27 s for one image and 5.78 s for 16 images. The script prints request and response SHA-256 digests, counts and timing, but not generated content. This is execution validation, not an accuracy or safety evaluation.

Only one activity should own the GPU at a time. Pause web jobs before training, then stop `vesta-web.service` and `vesta-inference.service` to free VRAM. Restart inference after training; start the web unit after inference is healthy. Avoid simultaneous fine-tuning and live analysis on this single GPU. Ollama at port 11435 is separate and must not be stopped, reconfigured, or used by Vesta.

For rollback, stop only the Vesta web unit and restore a previously verified code/config/model revision. Do not restore the earlier incorrect model-store path: it did not contain the intended vision model. Preserve runtime data and snapshot the SQLite database before schema changes. Disable either Vesta service independently without deleting media or model files.

```bash
systemctl --user status vesta-web vesta-inference --no-pager
journalctl --user -u vesta-web -n 50 --no-pager
systemctl --user stop vesta-web
systemctl --user restart vesta-inference vesta-web
```
