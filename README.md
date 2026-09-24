# easel-cli

A small CLI for generating images through [Easel](https://easel.ait4x.org/docs), an OpenAI-compatible image API backed by ComfyUI.

## Quick start

Set your API key in the environment or in a `.env` file in the directory where you run the command:

```dotenv
EASEL_API_KEY=your-key
```

Run directly from GitHub with [`uvx`](https://docs.astral.sh/uv/guides/tools/):

```bash
uvx --from git+https://github.com/venetanji/easel-cli easel generate "a red fox in a snowy forest" --output fox.png
```

The default endpoint is `https://easel.ait4x.org`. `uvx` installs the CLI in an isolated environment; it does not install anything into your project.

## Commands

```bash
easel generate "a red fox" --output fox.png
easel generate "a watercolor city" --model qwen-image-2.1 --size 1024x768 --steps 25 --seed 42 --output city.png
easel generate "three ideas" --n 3 --output idea.png
easel models
```

Supported model IDs are returned by `easel models` (currently `flux2-9b`, `flux2-4b`, and `qwen-image-2.1`). `--n` accepts 1–4; for multiple images, `--output idea.png` writes `idea-1.png`, `idea-2.png`, and so on. The API's default image size is used unless `--size WIDTHxHEIGHT` is specified.

### Options

- `--model`: model ID; default `flux2-9b`.
- `--size`: requested width and height, e.g. `1024x1024`.
- `--n`: number of images, 1–4.
- `--steps`: inference steps.
- `--seed`: integer seed.
- `--output`, `-o`: output filename; default `easel.png`.
- `--base-url`: API base URL; defaults to `EASEL_BASE_URL` or `https://easel.ait4x.org`.
- `--api-key`: bearer token; defaults to `EASEL_API_KEY` (or `EASEL_KEY`) in the environment or `.env`.

CLI flags override environment settings. The API key is sent only in the HTTPS `Authorization: Bearer` header. Do not commit `.env` files.

## Development

```bash
uv run --group dev pytest
uv run easel --help
```

## License

MIT
