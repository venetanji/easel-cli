# Easel CLI video workflows

Use the exact models returned by `easel models`. Check the selected endpoint
with `easel video capabilities --model MODEL` and
`easel video loras --model MODEL`. The examples submit a job once, after the
user authorizes generation. Use the printed receipt ID to resume.

## MiniMax H3

Choose **base** for text-to-video, first-image video, or a portrait speaking
from an existing local recording. Fetch `easel model-guide minimax-h3 --mode base`
before composing the prompt. For speech, supply `--input-reference` and
`--source-audio` and confirm the selected profile and source audio are
available.

Choose **reference** when a still supplies a subject or visual reference
(`--semantic-reference PATH`), or when consecutive stills belong at an
explicit pixel-frame position (`--temporal-group FRAME IMAGE [IMAGE ...]`).
Fetch `easel model-guide minimax-h3 --mode reference` before composing the prompt.
Repeat either flag within the advertised limits. Groups must fit in the clip,
use matching image dimensions within each group, and avoid overlap. Use
PNG/JPEG stills. Semantic and temporal references can be combined with each
other and, where supported, source audio. Keep `--input-reference` separate
from both reference flags.

Use `--frames` or the advertised default and check the endpoint's frame grid,
size and profile limits. Omit LTX-only `--seconds`, `--guide-frame`, camera,
LoRA, motion-speed and Ingredients flags.

```sh
easel video create "$PROMPT" --model minimax-h3 --frames 124 --size 768x512 \
  --input-reference portrait.png --source-audio speech.wav --receipt h3-speech.json

easel video create "$PROMPT" --model minimax-h3 --frames 124 --size 768x512 \
  --semantic-reference subject.png --temporal-group 0 opening.png \
  --temporal-group 123 closing.jpg --receipt h3-reference.json
```

## LTX 2.5

Use `--seconds` for the clip length and check the advertised size and limits.

- **First image and speech.** Use `--input-reference` for a starting still.
  Add `--source-audio` when the endpoint advertises
  `source_audio.available: true`. Omit H3's `--frames`,
  `--semantic-reference` and `--temporal-group`.
- **Timed stills.** Use `--guide-frame FRAME IMAGE STRENGTH` when
  `guiding_frames.supported` and `guiding_frames.available` are true. Use unique
  pixel-frame indices from 0 through `seconds * 24`, inclusive, and strengths
  from 0 to 1. Check the endpoint limit and stay within the CLI's eight-still
  cap. Keep guide frames separate from `--input-reference`, Ingredients and
  its reference-sheet flags. Slow motion and cinemagraph require a first
  image, so omit them in guide-frame mode.
- **Camera and LoRAs.** Use the camera shorthand `--camera-lora` with optional
  `--camera-lora-strength`, or select an advertised camera adapter with
  `--lora ID[=STRENGTH]`. Use each ID only once, including the shorthand,
  with up to four adapters total. Keep repeatable `--lora` separate from
  legacy `--loras` JSON. Ingredients needs `--lora-reference`, at least five
  seconds and no other LoRAs. Slow motion needs `--motion-speed` in
  `[0.025, 1]`. Cinemagraph conflicts with moving camera LoRAs. Check the
  endpoint's adapter requirements before combining controls.

```sh
easel video create "$PROMPT" --model ltx-2.5 --seconds 5 --size 768x512 \
  --input-reference first.png --source-audio speech.wav --receipt ltx-speech.json

easel video create "$PROMPT" --model ltx-2.5 --seconds 2 --size 512x320 \
  --lora camera-static=0.8 --guide-frame 1 opening.png 0.7 \
  --guide-frame 48 closing.jpg 0.3 --receipt ltx-guided.json
```

## Source audio on either model

Source audio conditions the video and supplies the trimmed soundtrack. Check
the advertised formats and upload limits. The recording must cover the full
rendered duration. At 24 fps the H3 example needs `124/24` seconds and the
LTX speech example needs `121/24` seconds, since LTX renders
`seconds * 24 + 1` frames. A six-second WAV covers either example.
