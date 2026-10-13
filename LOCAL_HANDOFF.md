# Fresh agent and local verification

Start in this repository and read README.md, then inspect `git status` before
editing. This CLI supports Qwen images and LTX/H3 generation, including H3 first-image
plus speech and typed semantic/temporal references. Retrieve the selected
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
audio; the CLI H3 `--model minimax-h3 --frames 124 --size 768x512`, first-image + WAV
recipe needs 124/24 seconds and must omit seconds. Source audio conditions motion
and supplies the trimmed soundtrack; mention visible articulation and pauses.
Use `model-guide minimax-h3 --mode base` and compose the prompt from that server
guide, including visible articulation and closed lips during pauses. Run:

```bash
easel video capabilities --model minimax-h3
easel video create "$PROMPT" --model minimax-h3 --frames 124 --size 768x512 \
  --input-reference first.png --source-audio speech.wav --receipt h3-job.json
```

Do not translate H3 frames into seconds or assume perfect lip-sync. H3 creation
checks one current exact-model capability snapshot and selected profile readiness;
source audio must be available if supplied. Frame/grid/seed/size/upload limits come
from the server. Missing, malformed, mismatched or unavailable required contracts
fail before POST, while ordinary LTX keeps its existing defaults and no discovery
requirement. Omit LTX camera/LoRA/motion/Ingredients/guide-frame controls for H3.
`--semantic-reference PATH` and `--temporal-group FRAME IMAGE [IMAGE ...]` are H3
only, capability-bounded and mutually exclusive with first-image input_reference.
Use the reference guide for these modes. PNG/JPEG only; each group shares geometry.
The server owns decoding, pixel limits and real runtime validation.


### Pinned offline contract CI

The existing CI workflow now runs the consumer's production adapter against
server commit `4c114e5642c7db875ffafb3ae3c70a21183032ad` with fake backends and installed ffmpeg/ffprobe.
The client job builds Media MCP before running the server's MCP contract suite;
the CLI job runs its local `contract/server_contract.py` with the server's dev
dependencies. An absent or invalid configured checkout fails the job.

The cross-repository checkout uses the existing GitHub token and checks the pinned
server source. Hosted server-contract CI passed at the previous CLI head
`0ff1be5173284b29dc47bccf3520ad3f51f2f106`. If checkout fails, CI fails visibly:
no skip, alternate unpinned source, or new secret is added. Local contract checks
also run independently of hosting. These are protocol and graph tests, not GPU
or visual lip-sync verification.
