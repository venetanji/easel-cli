# easel-cli

Images and videos through [Easel](https://easel.ait4x.org/docs). Current defaults are
`qwen-image-2.1` for images and `ltx-2.5` for video. Run `easel models` against your
endpoint to discover what is actually available; retired Flux IDs are no longer
valid for new requests.

## Quick start

Set `EASEL_API_KEY` in your local environment or a `.env` file in the working
directory. `EASEL_KEY` is also accepted. Never commit keys or `.env` files.

```bash
uvx --from git+https://github.com/venetanji/easel-cli easel models
uvx --from git+https://github.com/venetanji/easel-cli easel generate "a red fox" -o fox.png
```

The default endpoint is `https://easel.ait4x.org`. Override with `EASEL_BASE_URL`
or `--base-url`; a trailing `/v1` is accepted and normalized. HTTPS is required
except for loopback HTTP (`localhost`, `127.0.0.1`, `[::1]`) during local tests.
Connection flags belong after the leaf command. `--api-key` is available, but an
environment variable avoids putting the key in shell history/process arguments.

## Images

```bash
easel generate "a watercolor city" --size 512x512 --steps 25 --seed 42 -o city.png
easel generate "three ideas" --n 3 -o idea.png
easel edit "make the sky sunset" --image city.png -o sunset.png
easel variation city.png -o variation.png
```

`--n` accepts 1–4. Multiple images are numbered (`idea-1.png`, `idea-2.png`, etc.).
The server's default `|||` separator supports up to 16 prompts in one job;
multiple nonempty segments override `n`, and the CLI saves every returned image.
`edit --image` may be repeated for references (current Qwen limit: 16). Masked
inpainting is not supported. Optional `--server image|video` selects the image
backend override; it is normally best left unset. `--response-format url` uses
credentialed same-origin image downloads; synchronous base64 remains supported.

For durable, submit-once image generation, editing or variations:

```bash
easel generate "a red fox" --async --receipt fox-job.json
easel image-job status IMAGE_JOB_ID
easel image-job wait IMAGE_JOB_ID --wait-timeout 900 -o fox.png
easel image-job download IMAGE_JOB_ID -o fox-copy.png
# Or submit, save the receipt, poll and download in one invocation:
easel generate "a red fox" --wait --receipt fox-job.json -o fox.png
```

`--async` submits and exits. `--wait` implies async submission. The accepted
receipt is printed and flushed immediately, and optionally saved with
`--receipt`, before polling or downloading. Reuse the printed ID after a timeout
or interruption. Existing receipt/output files are never overwritten.

## Videos

```bash
easel video loras
easel video create "a red fox walking slowly" --seconds 2 --size 512x320 --seed 42 --receipt fox-video.json
easel video status VIDEO_ID
easel video queue VIDEO_ID
easel video wait VIDEO_ID --wait-timeout 1800 -o fox.mp4
easel video download VIDEO_ID -o fox-copy.mp4
# Image-to-video, with the same submit-once receipt flow:
easel video create "gentle motion" --input-reference fox.png --seconds 2 --size 512x320 --wait --receipt motion-job.json -o motion.mp4
```

Video creation uses multipart form data. Durations are whole seconds from 1–12
(default 4); default size is `1280x720`. `512x320` is useful for bounded smoke
tests. Nominal `1280x720` / `720x1280` currently decode at `1280x704` / `704x1280`.
At 24 FPS the server produces `seconds * 24 + 1` frames.

Camera flags: `--camera-lora static|dolly-in|dolly-out|dolly-left|dolly-right|jib-up|jib-down`
and optional `--camera-lora-strength` (0–2). The CLI also passes through the
server's `--loras` JSON, `--motion-speed`, `--lora-reference` and
`--lora-reference-strength`. Always inspect `video loras` and the
[server's adapter requirements](https://github.com/venetanji/easel/blob/10f118a818615595d53521d0ddae02b9c1d14d27/VIDEO_API.md)
first. Installed does not mean supported or visually validated. Unsupported
adapters, missing assets and invalid combinations are rejected by the server.

## Safety and recovery

- Generation POSTs are never retried automatically. Receipt loss is ambiguous:
  there is no idempotency, receipt-recovery or cancellation endpoint. A timeout
  or Ctrl-C stops client monitoring, not server execution.
- Status, queue and wait only GET existing IDs. Wait retries transient transport
  errors and HTTP 429/502/503/504, respecting `Retry-After`. Authentication,
  unknown/expired IDs and terminal failure/cancellation stop immediately.
- `--timeout` controls each HTTP request (default 600 seconds);
  `--wait-timeout` controls the polling budget (default 600), and
  `--poll-interval` defaults to 2 seconds. HTTPX timeouts are network-phase/read
  inactivity timeouts, so these are not a strict whole-operation wall-clock cap.
- Media downloads (including error bodies) and decoded image output are capped
  by `--max-download-mib` (default 512 MiB total image output or one video).
  JSON API responses are buffered before decoded-image validation; this flag
  is not a total process-memory limit.
- Bearer credentials go only to the configured Easel endpoint. Returned image
  URLs must match that origin and an image-content route. Redirects are never
  followed. CLI JSON/error output redacts known key echoes.
- Image receipts/cache expire after 24 hours (expired IDs: 410; unknown IDs:
  404). Completed cached images survive backend history loss. Video retention
  is also 24 hours, but still depends on ComfyUI history/output (unavailable or
  expired IDs: 404). Old accepted image jobs remain retrievable after their
  model is retired.
- Queue estimates are approximate time until completion, including generation;
  `null` means no estimate, not zero. They are not progress measurements.

This CLI covers Easel generation, reference image operations and job retrieval.
Timeline import, stitching and render belong to
[easel-client](https://github.com/venetanji/easel-client); downloaded files can be
used there. No live generation is part of the automated checks below.

## Development and contract verification

```bash
uv run --group dev pytest -q
uv build
uv run easel --help
uv run easel video create --help
```

The normal suite is entirely offline. An additional in-process contract suite
runs the actual Easel FastAPI app against fake ComfyUI backends, covering model
and LoRA discovery, synchronous images, async generation/edit/variation in both
response formats, T2V/I2V queue/status/content, and retired-model errors.
It requires a separate Easel checkout with its dev dependencies installed:

```bash
# From easel-cli, set this to your existing Easel checkout:
EASEL_SERVER_SOURCE=/path/to/easel PYTHONPATH=src \
  /path/to/easel/.venv/bin/python -m pytest -q contract/server_contract.py
```

Contract audited against Easel
[`10f118a`](https://github.com/venetanji/easel/commit/10f118a818615595d53521d0ddae02b9c1d14d27).
See the pinned [image-job contract](https://github.com/venetanji/easel/blob/10f118a818615595d53521d0ddae02b9c1d14d27/IMAGE_JOBS.md)
and [model-serving contract](https://github.com/venetanji/easel/blob/10f118a818615595d53521d0ddae02b9c1d14d27/MODEL_SERVING.md).
These checks prove protocol compatibility with fake output bytes; they do not
prove GPU execution, actual MP4 validity, visual quality or timeline rendering.

## License

MIT
