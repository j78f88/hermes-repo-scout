from __future__ import annotations

import json
from pathlib import Path

import click

from repo_scout.quality import run_quality_benchmark


@click.command()
@click.option("--out", "out_path", type=click.Path(dir_okay=False, path_type=Path), default=None, help="Optional JSON summary output path.")
def main(out_path: Path | None) -> None:
    """Print the network-free quality benchmark summary."""
    summary = run_quality_benchmark(output_path=out_path)
    click.echo(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
