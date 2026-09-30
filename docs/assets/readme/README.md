# README visuals

The two static images in the [README](../../../README.md) use system fonts,
contain no scripts or external resources, and include text alternatives. Their
source is here so a wording or behavior change can be reflected in the pictures.

## The two-session story

`working-together.svg.in` is the editable layout. [The demo](../../../tests/demo.py)
fills its claims, check output, receipt count and next task from a real scripted
run. It is an illustration, not a screenshot of a SAKO interface or a recording
of AI agents. [The transcript](../../coordination-demo.md) explains the limits.

Regenerate both files from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/demo.py --coordination > docs/coordination-demo.md
PYTHONDONTWRITEBYTECODE=1 python3 tests/demo.py --readme-svg > docs/assets/readme/working-together.svg
```

The existing documentation test reruns both outputs and compares them with the
published files. The separate finish-gate demo still uses
`python3 tests/demo.py > docs/demo.md`.

## How it works

`how-it-works.architecture.json` is an Archify 3.0.1 source with links to the
method and runtime at a pinned Git revision. It records the six roles and their
relationships. To build its interactive HTML with an installed Archify skill:

```sh
archify_entry=/path/to/archify/bin/archify.mjs
sako_preview=$(mktemp -d)
node "$archify_entry" finalize architecture \
  docs/assets/readme/how-it-works.architecture.json \
  "$sako_preview/how-it-works.html" --repo-root "$PWD" --quality showcase --json
```

Archify needs Chrome or Chromium for its browser check. If it cannot find yours,
set `ARCHIFY_CHROME` to the executable path. Its viewer exports SVG and PNG.

`how-it-works.svg` is an editable, compact adaptation of that model for the
README. It uses larger text, a vertical flow and plain role names so it works at
narrow widths. It is reviewed separately from Archify's canonical export. Edit
this SVG directly when changing its presentation. When behavior changes, update
the source references and both diagrams, rerun Archify's checks, and review the
SVG at desktop and mobile widths. Keep the same ownership: people direct agents;
SAKO reads or writes records, runs configured checks and reads Git. People or
agents commit.

## Review before publishing

Check the README at about 390 px and desktop width in light and dark themes.
Confirm every label fits, both images load, and the alt text and transcript carry
the same meaning. The README uses absolute image and documentation URLs because
it also supplies the PyPI description. A local preview must resolve new assets
from the checkout until they are pushed. Build the package and run
`uvx twine check --strict dist/*` before publishing its description.
