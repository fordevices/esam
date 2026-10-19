"""
Write the Cockpit as ONE self-contained HTML file with the current graph
baked in: no server, no database, no network needed to open it.

Use it to hand someone a picture of the baseline (attach it to a review,
open it on a laptop with no access to VM-1), or to publish a preview.

    python -m cockpit.export_snapshot                  # -> var/cockpit/ESAM_Cockpit_snapshot.html
    python -m cockpit.export_snapshot --out other.html

The file is a page fragment (no <html>/<head>); browsers open it fine.
Re-run after every ingest: a snapshot never updates itself.
"""

import argparse
from pathlib import Path

from esamkit.config import settings
from esamkit.factory import get_graph_store

from .app import DOC_TYPES, collect, last_runs, render_page

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "var" / "cockpit" / "ESAM_Cockpit_snapshot.html"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    data = collect(get_graph_store(), DOC_TYPES, last_runs(settings.pg_dsn))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_page(data, source="snapshot"))
    g = data["graph"]
    print(f"wrote {args.out}  ({len(g['vertices'])} vertices, {len(g['edges'])} edges, "
          f"taken {data['generated_at']})")


if __name__ == "__main__":
    main()
