#!/usr/bin/env python3
"""Synthesise a Saleae-style raw digital CSV for a known SPI waveform.

Two uses:

  * self-tests for the analysis tools (see test_analysis.py);
  * a dry run of the whole capture-and-analyse pipeline before the bench is
    wired, so the first real capture is not also the first time the analysis
    path has ever executed.

The generator models the defects the test plan predicts, so you can see what a
passing and a failing run each look like:

    # MR 1 baseline: bus runs, chip select never moves
    make_fixture.py --out mr1-b0.csv --mode 0 --hz 1e6 --bytes aa55aa55 --cs-dead

    # MR 2 T2 on the buggy build: mode 3 / 4 MHz requested, mode 0 / 1 MHz on the wire
    make_fixture.py --out mr2-b1-t2.csv --mode 0 --hz 1e6 --bytes aa55aa55aa55aa55

    # MR 2 T2 on the fixed build: what was asked for
    make_fixture.py --out mr2-b2-t2.csv --mode 3 --hz 4e6 --bytes aa55aa55aa55aa55
"""

from __future__ import annotations

import argparse
import csv
import sys

CHANNELS = ["SCLK", "MOSI", "MISO", "CE0", "CE1"]


def build(
    data: bytes,
    mode: int,
    hz: float,
    cs_dead: bool = False,
    loopback: bool = True,
    miso_data: bytes | None = None,
    lead_s: float = 20e-6,
    idle_high_cs: int = 1,
) -> list[tuple[float, dict[str, int]]]:
    cpol = mode >> 1
    cpha = mode & 1
    period = 1.0 / hz
    half = period / 2.0

    rx = miso_data if miso_data is not None else (data if loopback else b"\x00" * len(data))
    if len(rx) != len(data):
        raise ValueError("MISO byte count must match MOSI byte count")

    state = {"SCLK": cpol, "MOSI": 0, "MISO": 0, "CE0": 1, "CE1": 1}
    rows: list[tuple[float, dict[str, int]]] = [(0.0, dict(state))]

    def emit(t: float, **changes) -> None:
        state.update(changes)
        rows.append((t, dict(state)))

    t = lead_s
    if not cs_dead:
        emit(t, CE0=0)
    t += period  # setup time between CS assert and first clock

    tx_bits = [(b >> (7 - i)) & 1 for b in data for i in range(8)]
    rx_bits = [(b >> (7 - i)) & 1 for b in rx for i in range(8)]

    for tb, rb in zip(tx_bits, rx_bits):
        if cpha == 0:
            # Data is launched before the leading edge and sampled on it.
            emit(t, MOSI=tb, MISO=rb)
            emit(t + half, SCLK=1 - cpol)   # leading edge
            emit(t + period, SCLK=cpol)     # trailing edge
        else:
            # Data is launched on the leading edge and sampled on the trailing.
            # The launch is nudged a fixed fraction of a period past the edge
            # so the two events do not share a timestamp.  It must stay a
            # *fraction*, not a growing constant: a nudge that creeps toward
            # the sampling point eventually lands after it and corrupts the
            # last bits of a long payload at high clock rates.
            emit(t, SCLK=1 - cpol)          # leading edge
            emit(t + period * 0.02, MOSI=tb, MISO=rb)
            emit(t + half, SCLK=cpol)       # trailing edge
        t += period

    t += period
    if not cs_dead:
        emit(t, CE0=idle_high_cs)
    emit(t + lead_s, MOSI=0, MISO=0)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", type=int, default=0, choices=[0, 1, 2, 3],
                    help="mode present ON THE WIRE, which is not necessarily the one requested")
    ap.add_argument("--hz", type=float, default=1e6, help="clock rate on the wire")
    ap.add_argument("--bytes", default="aa55aa55", help="hex MOSI payload")
    ap.add_argument("--miso-bytes", default=None,
                    help="hex MISO payload; defaults to a MOSI loopback")
    ap.add_argument("--cs-dead", action="store_true",
                    help="chip select never toggles (the MR 1 baseline defect)")
    ap.add_argument("--no-loopback", action="store_true",
                    help="MISO stays idle instead of mirroring MOSI")
    args = ap.parse_args()

    data = bytes.fromhex(args.bytes)
    miso = bytes.fromhex(args.miso_bytes) if args.miso_bytes else None
    rows = build(
        data,
        mode=args.mode,
        hz=args.hz,
        cs_dead=args.cs_dead,
        loopback=not args.no_loopback,
        miso_data=miso,
    )

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Time [s]"] + CHANNELS)
        for t, state in rows:
            w.writerow([f"{t:.12f}"] + [state[c] for c in CHANNELS])

    print(f"{args.out}: {len(rows)} rows, mode {args.mode} on the wire, "
          f"{args.hz/1e6:g} MHz, {len(data)} bytes"
          f"{', CS dead' if args.cs_dead else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
