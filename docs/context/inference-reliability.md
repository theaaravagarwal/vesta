# Inference reliability investigation

This note records serving reliability observations for the dedicated Ollama
endpoint. It does not establish model accuracy, live-camera capacity, or a
production recovery guarantee.

## Observed failures

The host runs Ollama 0.33.2, `qwen2.5vl:3b`, one parallel request, and a
32,768-token context. The ignored host directory
`runtime/experiment-baseline-20260916T2316Z/` preserves the initial evidence.
In its `post-deploy-*` records, the first window of public ordinary clip
`sample05` failed with Vesta's explicit `malformed JSON` error. The Ollama log
for the corresponding request contains:

```
Unexpected empty grammar stack after accepting piece: ? (30)
```

Ollama returned HTTP 200 for that failed constrained-decoding request, so the
client could only report malformed content. Restarting `vesta-inference` once
was followed by a successful five-window replay, but that is a diagnostic
observation and is not an application recovery mechanism.

The 2026-09-16 follow-up used one 16-frame, eight-second public `sample05`
window while no web jobs were queued. A strict-schema request reached the
client's 180.114-second timeout and Ollama then logged an HTTP 500 at three
minutes. A later 190-second JSON-object-with-contract request showed the same
ordering: client timeout first, then an Ollama HTTP 500 at the client-bound
duration. These later 500s may be consequences of client cancellation, so they
do not independently establish an Ollama request limit or a recovery mechanism.
The next request was released by the server in 38.356 seconds after the first
failure, but the client diagnostic was intentionally interrupted to yield the
GPU and did not retain a parsed response. This is not proof that a retry works.

The exact grammar exception is also reported upstream for non-deterministic
grammar-constrained decoding, including prompt-dependent request outcomes in
[llama.cpp issue 27619](https://github.com/ggml-org/llama.cpp/issues/27619).
That report supports treating the exception as a serving/grammar defect. It
does not show that Vesta's prompt, frame count, focus transformation, token
count, or one particular JPEG is the cause.

## Current client behavior

`TemporalAnalyzer` keeps JSON-schema output and Vesta's strict event validator
for production analysis. It does not restart Ollama and it does not retry a
malformed response. The request timeout defaults to 180 seconds, bounded to
1–240 seconds by `BEHAVIOR_EVENT_REQUEST_TIMEOUT_S`. A 190-second probe still
timed out while Ollama returned its HTTP 500 at the same bound, so extending the
timeout did not improve diagnosis or recovery. HTTP failures record their
status when the client receives them; malformed HTTP-200 content and client
timeouts remain explicit failed windows.

## Checkpointed comparison

`scripts/inference_reliability_probe.py` is an opt-in host diagnostic. It
writes a small JSON record before sampling, before every call, and after every
outcome. The record contains no frame pixels, video paths, source checksum, or
model text. It includes frame dimensions and byte counts so a full/focus input
comparison can be checked after an interruption. A designated operator may add
`--retain-raw-response` to retain a raw response in that ignored runtime
artifact for diagnosis. It is never logged by the script, exposed by an API, or
committed.

The probe can compare the current `json_schema` request with Ollama's
`json_object` response format. `json_object` is a serving workaround candidate,
not a schema relaxation: both paths use `TemporalAnalyzer.infer` and therefore
run the same Vesta event-output validator. The `json-object-contract-*`
sequence applies the same event-field contract in the prompt; it does not alter
event meaning or validation. When `json_object` output is invalid, the record
includes structural types, field names, array counts, and string lengths. Do not promote it unless a bounded
comparison records successful, valid output through the intended mixed sequence
without a service restart.

On the host, with an explicitly selected public or consented source:

```bash
PATH=/home/software/vesta/.tools/bin:$PATH \
.venv/bin/python scripts/inference_reliability_probe.py \
  --video /path/to/selected.mp4 --start 0 --end 8 \
  --focus-box 0.20 0.20 0.80 0.80 \
  --output runtime/experiment-<timestamp>/inference-format.json \
  --sequence strict-full,strict-focus,strict-full,json-object-full
```

Run only while the designated worker owns the GPU. Preserve every completed or
interrupted record under ignored `runtime/`; do not commit media or diagnostic
model content.
