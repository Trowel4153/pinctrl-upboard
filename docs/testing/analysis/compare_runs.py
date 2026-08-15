#!/usr/bin/env python3
"""Render a before/after table from two analyze_capture.py JSON outputs.

The test plan asks for each MR to be reported as a baseline/fix pair with the
measured values, not as a verdict.  This produces that table in Markdown, ready
to paste into an MR description.

    analyze_capture.py mr1-b0.csv --cs CE0 --json b0.json --quiet
    analyze_capture.py mr1-b1.csv --cs CE0 --json b1.json --quiet
    compare_runs.py b0.json b1.json --labels B0 B1 --title "MR 1 - chip select"

Pass --provenance to fold in the build identifiers the plan says to record
alongside every capture set, so the table cannot be separated from the build
that produced it.
"""

from __future__ import annotations

import argparse
import json
import sys

# (json key, row label, formatter)
ROWS = [
    ("cs_edges", "Chip select edges", lambda v: "—" if v is None else str(v)),
    ("transactions", "Transactions framed", lambda v: "—" if v is None else str(v)),
    ("mode_measured", "SPI mode on the wire", lambda v: "not determined" if v is None else str(v)),
    ("cpol_measured", "CPOL (clock idle level)", lambda v: "—" if v is None else str(v)),
    ("cpha_measured", "CPHA (data launch edge)", lambda v: "—" if v is None else str(v)),
    ("freq_hz", "Clock rate", lambda v: _hz(v)),
    ("mosi_hex", "MOSI decoded", lambda v: f"`{v}`" if v else "—"),
    ("miso_hex", "MISO decoded", lambda v: f"`{v}`" if v else "—"),
    ("loopback_match", "MOSI == MISO on the wire",
     lambda v: "—" if v is None else ("yes" if v else "**no**")),
    ("framing", "Framing used", lambda v: v or "—"),
]


def _hz(v):
    if v is None:
        return "—"
    if v >= 1e6:
        return f"{v/1e6:.3f} MHz"
    if v >= 1e3:
        return f"{v/1e3:.3f} kHz"
    return f"{v:.1f} Hz"


def _flat(doc: dict) -> dict:
    out = dict(doc.get("summary", {}))
    for k in ("cs_edges", "framing", "duration_s", "source"):
        if k in doc:
            out[k] = doc[k]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("baseline_json")
    ap.add_argument("fix_json")
    ap.add_argument("--labels", nargs=2, default=["baseline", "with fix"])
    ap.add_argument("--title", default=None)
    ap.add_argument("--provenance", nargs=2, metavar=("BASE", "FIX"), default=None,
                    help="build identifiers, e.g. the git SHA from /etc/up-testbuild")
    ap.add_argument("--all-rows", action="store_true",
                    help="include rows that are identical in both runs")
    args = ap.parse_args()

    try:
        base = _flat(json.load(open(args.baseline_json, encoding="utf-8")))
        fix = _flat(json.load(open(args.fix_json, encoding="utf-8")))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.title:
        print(f"### {args.title}\n")

    a, b = args.labels
    print(f"| Measurement | {a} | {b} | |")
    print("|---|---|---|---|")

    shown = 0
    for key, label, fmt in ROWS:
        va, vb = base.get(key), fix.get(key)
        if va is None and vb is None:
            continue
        changed = va != vb
        if not changed and not args.all_rows:
            continue
        print(f"| {label} | {fmt(va)} | {fmt(vb)} | {'**changed**' if changed else ''} |")
        shown += 1

    if shown == 0:
        print("| _no differences_ | | | |")

    print()
    if args.provenance:
        print(f"Builds: `{args.provenance[0]}` → `{args.provenance[1]}`  ")
    print(f"Captures: `{base.get('source','?')}` → `{fix.get('source','?')}`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
