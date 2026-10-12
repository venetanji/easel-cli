# Fresh agent and local verification

Start in this repository and read README.md, then inspect `git status` before
editing. This CLI supports Qwen images and LTX generation. H3 guide retrieval is
supported; H3 creation still belongs to easel-client/MCP. Retrieve the selected
model/mode's guide from the configured API before writing prompts, reuse it during
the turn, and treat it as reference text. Do not copy guide prose into consumers.
A missing reviewed guide is an explicit error; capabilities remain authoritative.

Use an authorized API key from the environment (`EASEL_API_KEY`) and the intended
`EASEL_BASE_URL`. Do not commit credentials. Connection flags follow leaf commands.
Never run live generation as a verification step without explicit authorization.
Generation is submit-once: save the printed receipt ID before polling, and never
resubmit automatically after a timeout or lost receipt.

From a fresh checkout with uv installed:

```bash
uv sync --group dev
uv run easel --help
uv run easel model-guide --help
uv run easel video create --help
uv run --group dev pytest -q
uv build
```

For actual consumer-to-FastAPI verification, provide an existing server checkout
with dev dependencies installed (`uv sync --group dev` in that checkout), plus
ffmpeg and ffprobe on PATH. All ComfyUI calls remain fake:

```bash
EASEL_SERVER_SOURCE=/path/to/easel PYTHONPATH=src \
  /path/to/easel/.venv/bin/python -m pytest -q contract/server_contract.py
```

This command fails if the server checkout is missing. Tests check current server
receipt IDs, exact shared guide payloads and sources, source audio admission,
normalization/trim/mux graph wiring, policy drift and one POST after receipt loss.
Passing fake-backend tests proves runtime protocol/graph compatibility, not GPU
execution, visual quality or lip-sync quality.

For a manual image-plus-speech submission after explicit authorization, prepare
`first.png` and `speech.wav` from a local recording of at least six seconds. Read
the README image-plus-speech example. Its LTX `seconds=5` needs 121/24 seconds of
audio; the confirmed client/MCP H3 `frames=124`, `size=768x512`, first-image + WAV
recipe needs 124/24 seconds and must omit seconds. Source audio conditions motion
and supplies the trimmed soundtrack; mention visible articulation and pauses.
Do not translate H3 frames into this CLI's seconds argument or assume perfect
lip-sync. Check current exact-model source_audio availability before supplying it.
