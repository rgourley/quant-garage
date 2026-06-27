"""
Uniform stdout output-mode control for the examples/run-*.py scripts.

Every runner writes a markdown file (canonical JSON + rendered text) AND
prints something to stdout. By default that stdout content is the rendered
text (human-friendly). Agents piping a script's output into another tool
need a way to ask for *just* the canonical JSON without parsing markdown.

Resolution order (highest first):
1. `--format <render|json|both>` CLI flag (where argparse is in use)
2. `QUANT_GARAGE_OUTPUT_FORMAT` env var
3. default: `render`

The file write is unchanged — the disk artifact always carries both layers.
This module only governs what reaches stdout.
"""

from __future__ import annotations

import json as _json
import os
import sys
from typing import Any, Literal

OutputFormat = Literal["render", "json", "both"]

_VALID: tuple[OutputFormat, ...] = ("render", "json", "both")
_ENV_VAR = "QUANT_GARAGE_OUTPUT_FORMAT"


def resolve_output_format(cli_value: str | None = None) -> OutputFormat:
    """Pick the output format. CLI flag > env var > default 'render'.

    Raises SystemExit with a clear message if an invalid value is supplied
    via either source.
    """
    if cli_value is not None:
        if cli_value not in _VALID:
            raise SystemExit(
                f"--format must be one of {_VALID}, got {cli_value!r}"
            )
        return cli_value  # type: ignore[return-value]
    env = os.environ.get(_ENV_VAR)
    if env:
        if env not in _VALID:
            raise SystemExit(
                f"{_ENV_VAR} must be one of {_VALID}, got {env!r}"
            )
        return env  # type: ignore[return-value]
    return "render"


def emit_to_stdout(rendered: str, payload: Any, fmt: OutputFormat) -> None:
    """Write the requested view of the run to stdout.

    - render: rendered text only (current default behavior).
    - json:   canonical JSON only, indented, with a terminating newline.
    - both:   rendered text, a blank line, then the JSON.

    `payload` is typed as `Any` because the canonical JSON shape varies per
    skill; `json.dumps(..., default=str)` handles datetimes/Decimals/etc.
    """
    if fmt == "render":
        print(rendered)
        return
    if fmt == "json":
        print(_json.dumps(payload, indent=2, default=str))
        return
    # both
    print(rendered)
    print()
    print(_json.dumps(payload, indent=2, default=str))
