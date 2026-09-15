"""Camera-free GPU inference and backward-pass checks for the compute host.

Run from repository root: .venv/bin/python scripts/compute-check.py
This checks execution, not detector accuracy or training quality.
"""

from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import time

import numpy as np
import torch
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / "runtime" / "ultralytics"))
os.environ["VESTA_DISABLE_BEHAVIOR_WORKER"] = "1"
sys.path.insert(0, str(ROOT))
import main


def check() -> None:
    assert torch.cuda.is_available(), "CUDA unavailable"
    detector = main.get_model()
    results = main.safe_yolo_predict(
        detector,
        source=np.zeros((640, 640, 3), dtype=np.uint8),
        device=main.inference_device,
        half=main.inference_half,
        verbose=False,
        imgsz=640,
    )
    assert len(results) == 1, "Missing detector result"
    layer = torch.nn.Linear(16, 4).cuda()
    optimizer = torch.optim.SGD(layer.parameters(), lr=0.01)
    before = layer.weight.detach().clone()
    loss = layer(torch.ones(2, 16, device="cuda")).square().mean()
    assert torch.isfinite(loss), "Nonfinite training loss"
    loss.backward()
    optimizer.step()
    torch.cuda.synchronize()
    assert not torch.equal(before, layer.weight), "Optimizer did not update weights"
    print(f"PASS GPU: {torch.cuda.get_device_name(0)}")
    print(f"PASS detector: {main.loaded_model_path.name} on {main.inference_device}")
    print("PASS synthetic backward/optimizer step; no dataset or trained artifact")


def gpu_memory_mib() -> str:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().splitlines()[0]


def check_vlm_payload(image_count: int) -> None:
    captured: list[dict[str, object]] = []
    original_urlopen = main.request.urlopen

    def capture_request(req, *args, **kwargs):
        body = req.data
        payload = json.loads(body)
        messages = payload["messages"]
        content = messages[-1]["content"]
        n_images = sum(part.get("type") == "image_url" for part in content)
        if n_images != image_count:
            raise AssertionError(f"expected {image_count} images, got {n_images}")
        if payload["model"] != main.LLAMACPP_MODEL:
            raise AssertionError("unexpected configured VLM model")
        captured.append(
            {
                "sha256": hashlib.sha256(body).hexdigest(),
                "image_count": n_images,
                "model": payload["model"],
                "endpoint": req.full_url,
            }
        )
        return original_urlopen(req, *args, **kwargs)

    main.request.urlopen = capture_request
    try:
        images = []
        for i in range(image_count):
            image = Image.new(
                "RGB", (96, 96), (i * 13 % 256, i * 29 % 256, i * 47 % 256)
            )
            draw = ImageDraw.Draw(image)
            draw.rectangle((8 + i % 24, 12, 48, 72), outline="white", width=3)
            images.append(image)
        before = gpu_memory_mib()
        started = time.perf_counter()
        answer = main.query_llamacpp_with_images(
            images,
            "Describe the synthetic test image(s) in one short sentence.",
            max_tokens=64,
            temperature=0.0,
        )
        elapsed = time.perf_counter() - started
        after = gpu_memory_mib()
    finally:
        main.request.urlopen = original_urlopen

    assert answer.strip(), "VLM returned no text"
    assert len(captured) == 1, "request was not captured exactly once"
    meta = captured[0]
    print(
        f"PASS synthetic VLM images={image_count} model={meta['model']} "
        f"payload_sha256={meta['sha256']} elapsed_s={elapsed:.2f} "
        f"gpu_mem_mib_before={before} after={after} "
        f"response_sha256={hashlib.sha256(answer.encode()).hexdigest()}"
    )


def check_vlm() -> None:
    check_vlm_payload(1)
    check_vlm_payload(16)


if __name__ == "__main__":
    check()
    check_vlm()
