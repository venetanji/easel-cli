# Licensing audit

Reviewed 2026-10-05 against `main` at
[`ba2400ce22f1a4c9da4d6f5d2855aee309d7ffd9`](https://github.com/venetanji/easel-cli/commit/ba2400ce22f1a4c9da4d6f5d2855aee309d7ffd9).
This is a repository and packaging review, not a legal opinion or a claim to
have established the provenance of every line independently.

## Project grant and historical rights

- The project grant is **GPL-3.0-or-later**, stated in `NOTICE`, `README.md`, and
  Python package metadata. `LICENSE` contains the complete, unmodified
  [GNU GPL version 3 text](https://www.gnu.org/licenses/gpl-3.0.txt). The
  "or later" choice is made by the project grant in `NOTICE`.
- The earlier `LICENSE` was MIT, with `Copyright (c) 2026 venetanji`.
  Its entire contents are preserved byte-for-byte in `LICENSES/legacy-MIT.txt`.
  Existing MIT grants for material originating in that revision remain valid;
  this change does not revoke them or convert third-party licenses into GPL.
- The project grant covers project-maintained source, tests, documentation, and
  configuration. Separately licensed material and historical notices keep their
  stated terms.

## Provenance review

The review used the tracked-file inventory, the complete history reachable from
the baseline, all source and test files, configuration, lockfile, and searches
for copyright, SPDX, license, copied/derived/vendor markers, and source URLs.
It did not rely on the Git author list alone.

- The baseline has 13 tracked regular files and one reachable commit, authored
  by `venetanji`. No submodules or Git LFS pointers are present.
- `src/easel_cli/` contains four Python files: package and command entry points,
  CLI argument/environment/output handling, and an HTTP API client. The only
  non-standard-library runtime import is `httpx`.
- `tests/` contains two API/CLI test files using local fake responses. The
  remaining files are the README, MIT license, project/CI configuration,
  environment example, ignore rules, and `uv.lock`.
- No vendored dependency source, copied-code attribution, third-party copyright
  notice, binary executable, image asset, model weights, or font was found in
  these tracked files. Absence of a marker is not proof of original authorship.
- The CI file references `actions/checkout` and `astral-sh/setup-uv`; their code
  is fetched by CI and is not part of the Python distribution.
- Easel/ComfyUI and model names in the README describe the remote service. The
  client does not bundle their implementation or model weights. API compatibility
  does not grant rights to those external projects or generated content.
- Other unmerged branches, external repositories, and remote server deployments
  were not included in this audit. Recheck provenance when integrating them.

## Dependency boundary

`httpx>=0.27` remains the sole declared runtime dependency. `uv.lock` was not
changed by this licensing update. The locked environment's installed package
metadata reports the following licenses (a metadata inventory, not a complete
file-level audit of those distributions):

| Runtime package | Locked version | Reported license |
| --- | --- | --- |
| httpx | 0.28.1 | BSD-3-Clause |
| httpcore | 1.0.9 | BSD-3-Clause |
| anyio | 4.15.1 | MIT |
| certifi | 2026.7.22 | MPL-2.0 |
| h11 | 0.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| typing_extensions | 4.16.0 | PSF-2.0 |

The development environment additionally reports pytest 9.1.1 (MIT),
iniconfig 2.3.0 (MIT), pluggy 1.6.0 (MIT), Pygments 2.21.0 (BSD-2-Clause),
and packaging 26.3 (Apache-2.0 OR BSD-2-Clause). The Windows-only locked
`colorama` dependency was not installed in this Linux review. Hatchling is a
separate build dependency; its minimum version is 1.26 for PEP 639 metadata.

These packages keep their own terms, including notices and data-specific terms
inside their distributions. For example, `certifi`'s certificate bundle remains
under its upstream terms. Redistributing dependencies or a bundled executable
requires reviewing and preserving the actual dependency distributions' notices
and any applicable source obligations. This PR does not declare a blanket
license for the whole dependency graph.

## Distribution verification

- Baseline: 8 existing tests passed, and a wheel and source distribution built.
- Regression tests were first run against the baseline and failed for missing
  GPL metadata, the old root license, the missing legacy notice, and the old
  README statement. Run them with `uv run --locked --group dev pytest`.
- Build both artifacts with `uv build`. Inspect the wheel's `.dist-info/METADATA`
  and the source distribution's `PKG-INFO`: both must declare
  `License-Expression: GPL-3.0-or-later` and all three `License-File` entries.
- Both artifacts must include `LICENSE`, `NOTICE`, and
  `LICENSES/legacy-MIT.txt` (under `.dist-info/licenses/` in the wheel), with
  contents identical to the source files. The source archive also contains the
  audit and tests. The wheel contains only the `easel_cli` package and its
  distribution metadata, with no bundled dependencies or model assets.
- `tests/test_licensing.py` pins the complete GPL and historical MIT text by
  SHA-256 (normalizing platform line endings) and checks project metadata and
  notice/README consistency.
- After the change, all 12 tests passed; the wheel and source archive built and
  passed the metadata, notice-content, and file-inventory checks above. The CLI
  help smoke test also passed. These checks do not replace a dependency audit.

## Distributing corresponding source

Keep the exact source revision used for each distributed artifact. An archive or
immutable commit reference must match the artifact, including any local changes;
a link to the moving `main` branch is not sufficient. Preserve the relevant
project source, `pyproject.toml`, `uv.lock`, tests, build/CI configuration, license
files, and the instructions needed to build and install it. From that source:

```bash
uv sync --locked --group dev
uv run --locked --group dev pytest
uv build
uv tool install ./dist/easel_cli-0.1.0-py3-none-any.whl
```

Use the matching filename if the project version changes. Record the build
toolchain and any additional scripts or changes used for a release. A source
archive produced here contains the project build inputs; it does not include
the source of separately installed dependencies.

If distributing a future bundled executable or another object-code form to
which [GPL section 6](https://www.gnu.org/licenses/gpl-3.0.html#section6) applies,
choose and fulfill an applicable corresponding-source delivery option. For
example, section 6(d) allows equivalent source access from the same designated
download location at no further charge. Include all corresponding source and
build/install material required by that distribution, assess dependency source
and notice obligations separately, and provide Installation Information when
required. Merely linking this repository does not establish compliance for a
different or bundled binary. No executable bundling or release is introduced
by this change.

Packaging follows the [PyPA license metadata guidance](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#license-and-license-files).
No package version was bumped, and no release or publication is part of this
change. Future license scans should allow the retained MIT notice and keep the
project grant distinct from dependency licenses.
