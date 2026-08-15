#!/usr/bin/env python3
"""Measure one Saleae raw digital CSV and assert the test plan's expectations.

Reports what is actually on the wire — chip select activity, CPOL, CPHA, clock
rate, decoded MOSI/MISO — and optionally checks that against what the transfer
asked for.  Exit status is what an agent should key on:

  0  every stated expectation held
  1  a measurement contradicted an expectation
  2  the capture could not be analysed at all

Examples
--------
MR 1 baseline, asserting that chip select is dead but data is still on the bus:

    analyze_capture.py mr1-b0.csv --cs CE0 --expect-cs-edges 0 --expect-mosi aa55aa55

MR 1 after the fix:

    analyze_capture.py mr1-b1.csv --cs CE0 --expect-cs-edges 2 --expect-mosi aa55aa55

MR 2 T2, where mode 3 at 4 MHz was requested.  On the buggy build this exits 1
and names the mismatch; on the fixed build it exits 0:

    analyze_capture.py mr2-t2.csv --cs CE0 --expect-mode 3 --expect-hz 4e6

MR 3, proving the bytes were on the wire even when the driver returned garbage:

    analyze_capture.py mr3-100k.csv --cs CE0 --expect-loopback --expect-hz 100e3 --hz-tolerance 0.2
"""

from __future__ import annotations

import argparse
import json
import sys

import salcap


def _fmt_hz(v):
    if v is None:
        return "n/a"
    if v >= 1e6:
        return f"{v/1e6:.3f} MHz"
    if v >= 1e3:
        return f"{v/1e3:.3f} kHz"
    return f"{v:.1f} Hz"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("csv", help="Saleae RAW DIGITAL export (not the analyzer export)")
    ap.add_argument("--sclk", default="SCLK")
    ap.add_argument("--mosi", default="MOSI")
    ap.add_argument("--miso", default="MISO")
    ap.add_argument("--cs", default=None, help="chip select channel, e.g. CE0")
    ap.add_argument("--bits", type=int, default=8, dest="bits_per_word")
    ap.add_argument("--assume-mode", type=int, choices=[0, 1, 2, 3], default=None,
                    help="decode with this mode rather than the one measured; "
                         "use it to show that a wrong-mode decode yields garbage")
    ap.add_argument("--json", metavar="PATH", help="write the full measurement as JSON")
    ap.add_argument("--quiet", action="store_true")

    exp = ap.add_argument_group("expectations")
    exp.add_argument("--expect-cs-edges", type=int)
    exp.add_argument("--expect-mode", type=int, choices=[0, 1, 2, 3])
    exp.add_argument("--expect-hz", type=float)
    exp.add_argument("--hz-tolerance", type=float, default=0.15,
                     help="fractional tolerance for --expect-hz (default 0.15)")
    exp.add_argument("--expect-mosi", help="hex bytes expected on MOSI")
    exp.add_argument("--expect-miso", help="hex bytes expected on MISO")
    exp.add_argument("--expect-loopback", action="store_true",
                     help="require MOSI and MISO to carry identical bytes")
    exp.add_argument("--expect-transactions", type=int)

    args = ap.parse_args()

    try:
        cap = salcap.Capture.from_csv(args.csv)
        result = salcap.analyse(
            cap,
            sclk=args.sclk,
            mosi=args.mosi,
            miso=args.miso,
            cs=args.cs,
            assume_mode=args.assume_mode,
            bits_per_word=args.bits_per_word,
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    s = result["summary"]
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, ok, detail))

    if args.expect_cs_edges is not None:
        got = result.get("cs_edges")
        check("cs-edges", got == args.expect_cs_edges,
              f"expected {args.expect_cs_edges}, measured {got}")

    if args.expect_transactions is not None:
        check("transactions", s.get("transactions") == args.expect_transactions,
              f"expected {args.expect_transactions}, measured {s.get('transactions')}")

    if args.expect_mode is not None:
        check("mode", s.get("mode_measured") == args.expect_mode,
              f"expected mode {args.expect_mode} "
              f"(cpol={args.expect_mode>>1} cpha={args.expect_mode&1}), "
              f"measured {s.get('mode_measured')} "
              f"(cpol={s.get('cpol_measured')} cpha={s.get('cpha_measured')})")

    if args.expect_hz is not None:
        got = s.get("freq_hz")
        ok = got is not None and abs(got - args.expect_hz) <= args.expect_hz * args.hz_tolerance
        check("clock", ok,
              f"expected {_fmt_hz(args.expect_hz)} "
              f"+/-{args.hz_tolerance*100:g}%, measured {_fmt_hz(got)}")

    if args.expect_mosi is not None:
        want = args.expect_mosi.lower().replace(" ", "")
        check("mosi", s.get("mosi_hex") == want,
              f"expected {want}, decoded {s.get('mosi_hex')}")

    if args.expect_miso is not None:
        want = args.expect_miso.lower().replace(" ", "")
        check("miso", s.get("miso_hex") == want,
              f"expected {want}, decoded {s.get('miso_hex')}")

    if args.expect_loopback:
        check("loopback", s.get("loopback_match") is True,
              f"MOSI {s.get('mosi_hex')} vs MISO {s.get('miso_hex')}")

    if not args.quiet:
        print(f"capture      {args.csv}")
        print(f"duration     {result['duration_s']*1e3:.3f} ms")
        print(f"framing      {result.get('framing')}")
        if "cs_edges" in result:
            print(f"cs edges     {result['cs_edges']} on {result['cs_channel']}"
                  f"   ({'toggles' if result['cs_asserted'] else 'DEAD - never moves'})")
        print(f"transactions {s.get('transactions')}")
        print(f"mode on wire {s.get('mode_measured')} "
              f"(cpol={s.get('cpol_measured')} cpha={s.get('cpha_measured')})")
        print(f"clock        {_fmt_hz(s.get('freq_hz'))}")
        if args.assume_mode is not None:
            print(f"decoded with mode {args.assume_mode} (forced)")
        print(f"mosi         {s.get('mosi_hex')}")
        print(f"miso         {s.get('miso_hex')}")
        if s.get("loopback_match") is not None:
            print(f"loopback     {'MATCH' if s['loopback_match'] else 'MISMATCH'}")
        for tx in result["transactions"]:
            ph = tx.get("phase") or {}
            if ph.get("confidence") is not None and ph["confidence"] < 0.9:
                print(f"warning      weak CPHA inference ({ph['confidence']:.0%} of "
                      f"{ph['samples']} data transitions agree); use a pattern "
                      f"like aa55 that toggles every bit")
            if not tx["clock"]["idle_consistent"]:
                print("warning      clock idle level differs before and after the "
                      "transaction; check the probe and the logic threshold")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        if not args.quiet:
            print(f"json         {args.json}")

    if checks:
        print()
        for name, ok, detail in checks:
            print(f"{'PASS' if ok else 'FAIL'}  {name:14s} {detail}")
        if not all(ok for _, ok, _ in checks):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
