#!/usr/bin/env python3
"""Self-tests for the capture analysis tools.

Builds synthetic captures for each scenario the test plan predicts and checks
that the analysis reports what the plan says it should.  No hardware, no
Saleae software.  Run it before the bench session:

    python3 test_analysis.py

If this passes, a surprising result on real hardware is evidence about the
driver rather than about the analysis code.
"""

from __future__ import annotations

import csv
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import make_fixture
import salcap

PASS = "\033[32mok\033[0m" if sys.stdout.isatty() else "ok"
FAIL = "\033[31mFAILED\033[0m" if sys.stdout.isatty() else "FAILED"

_failures: list[str] = []


def fixture(tmp: Path, name: str, **kw) -> salcap.Capture:
    data = bytes.fromhex(kw.pop("payload", "aa55aa55"))
    rows = make_fixture.build(data, **kw)
    path = tmp / f"{name}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Time [s]"] + make_fixture.CHANNELS)
        for t, state in rows:
            w.writerow([f"{t:.12f}"] + [state[c] for c in make_fixture.CHANNELS])
    return salcap.Capture.from_csv(str(path))


def expect(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def expect_close(label: str, got, want, tol=0.02) -> None:
    ok = got is not None and abs(got - want) <= abs(want) * tol
    if ok:
        print(f"  {PASS}  {label} ({got:.4g})")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want ~{want!r}")
        _failures.append(label)


def run(tmp: Path) -> None:
    analyse = lambda cap, **kw: salcap.analyse(
        cap, sclk="SCLK", mosi="MOSI", miso="MISO", **kw
    )

    print("\nparser")
    cap = fixture(tmp, "parse", mode=0, hz=1e6)
    expect("channels found", cap.names, make_fixture.CHANNELS)
    expect("resolve by short name", cap.resolve("d0"), "SCLK")
    expect("resolve by exact name", cap.resolve("CE0"), "CE0")

    print("\nMR 1 baseline: chip select never toggles")
    cap = fixture(tmp, "mr1_b0", mode=0, hz=1e6, cs_dead=True)
    r = analyse(cap, cs="CE0")
    expect("cs edges", r["cs_edges"], 0)
    expect("cs reported dead", r["cs_asserted"], False)
    expect("falls back to clock-burst framing", r["framing"], "clock-burst")
    expect("data still decodes off the wire", r["summary"]["mosi_hex"], "aa55aa55")

    print("\nMR 1 fixed: chip select frames the transfer")
    cap = fixture(tmp, "mr1_b1", mode=0, hz=1e6)
    r = analyse(cap, cs="CE0")
    expect("cs edges", r["cs_edges"], 2)
    expect("one transaction", r["summary"]["transactions"], 1)
    expect("chip-select framing", r["framing"], "chip-select")
    expect("mosi decodes", r["summary"]["mosi_hex"], "aa55aa55")

    print("\nmode detection, all four modes")
    for mode in (0, 1, 2, 3):
        cap = fixture(tmp, f"mode{mode}", mode=mode, hz=1e6, payload="aa55aa55")
        r = analyse(cap, cs="CE0")
        s = r["summary"]
        expect(f"mode {mode}: cpol", s["cpol_measured"], mode >> 1)
        expect(f"mode {mode}: cpha", s["cpha_measured"], mode & 1)
        expect(f"mode {mode}: decode", s["mosi_hex"], "aa55aa55")

    print("\nMR 2 T2 on the buggy build: asked for mode 3 / 4 MHz, wire shows mode 0 / 1 MHz")
    cap = fixture(tmp, "mr2_b1_t2", mode=0, hz=1e6, payload="aa55aa55aa55aa55")
    r = analyse(cap, cs="CE0")
    s = r["summary"]
    expect("measured mode contradicts the request", s["mode_measured"], 0)
    expect_close("measured clock contradicts the request", s["freq_hz"], 1e6)

    print("\nMR 2 T2 on the fixed build: mode 3 / 4 MHz as requested")
    cap = fixture(tmp, "mr2_b2_t2", mode=3, hz=4e6, payload="aa55aa55aa55aa55")
    r = analyse(cap, cs="CE0")
    s = r["summary"]
    expect("mode", s["mode_measured"], 3)
    expect_close("clock", s["freq_hz"], 4e6)
    expect("decode", s["mosi_hex"], "aa55aa55aa55aa55")

    print("\nMR 2 T5 on the buggy build: asked for mode 0 / 1 MHz, gets T3's stale mode 3 / 4 MHz")
    cap = fixture(tmp, "mr2_b1_t5", mode=3, hz=4e6)
    r = analyse(cap, cs="CE0")
    expect("stale mode visible", r["summary"]["mode_measured"], 3)
    expect_close("stale clock visible", r["summary"]["freq_hz"], 4e6)

    print("\nthe decoded bytes do NOT identify the mode; the measurement does")
    # Data that is held for a whole clock period is sampled correctly by either
    # edge, so every assumed mode decodes the same bytes.  This is why the mode
    # test has to assert on cpol/cpha/frequency and never on the payload.
    cap = fixture(tmp, "wrongmode", mode=0, hz=1e6, payload="aa55aa55")
    decodes = {m: analyse(cap, cs="CE0", assume_mode=m)["summary"]["mosi_hex"]
               for m in (0, 1, 2, 3)}
    expect("all four assumed modes decode the same bytes",
           set(decodes.values()), {"aa55aa55"})
    expect("but the measured mode is unambiguous",
           analyse(cap, cs="CE0")["summary"]["mode_measured"], 0)

    print("\nMR 3: loopback on the wire at low clock rates")
    for hz in (4e6, 1e6, 100e3, 25e3):
        cap = fixture(tmp, f"mr3_{hz:.0f}", mode=0, hz=hz, payload="deadbeef")
        r = analyse(cap, cs="CE0")
        s = r["summary"]
        expect(f"{hz/1e3:g} kHz: mosi", s["mosi_hex"], "deadbeef")
        expect(f"{hz/1e3:g} kHz: miso mirrors mosi", s["loopback_match"], True)
        expect_close(f"{hz/1e3:g} kHz: measured rate", s["freq_hz"], hz)

    print("\nMR 3: a non-loopback capture is reported as a mismatch")
    cap = fixture(tmp, "mr3_bad", mode=0, hz=100e3, payload="deadbeef",
                  miso_data=bytes.fromhex("00000000"))
    r = analyse(cap, cs="CE0")
    expect("loopback mismatch detected", r["summary"]["loopback_match"], False)
    expect("miso decodes as the idle pattern", r["summary"]["miso_hex"], "00000000")

    print("\nmulti-transaction capture")
    rows_a = make_fixture.build(bytes.fromhex("aa55"), mode=0, hz=1e6)
    offset = rows_a[-1][0] + 100e-6
    rows_b = [(t + offset, s) for t, s in
              make_fixture.build(bytes.fromhex("1234"), mode=0, hz=1e6)[1:]]
    path = tmp / "multi.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Time [s]"] + make_fixture.CHANNELS)
        for t, state in rows_a + rows_b:
            w.writerow([f"{t:.12f}"] + [state[c] for c in make_fixture.CHANNELS])
    r = analyse(salcap.Capture.from_csv(str(path)), cs="CE0")
    expect("two transactions", r["summary"]["transactions"], 2)
    expect("first payload", r["transactions"][0]["mosi"]["hex"], "aa55")
    expect("second payload", r["transactions"][1]["mosi"]["hex"], "1234")

    print("\nheaderless and boolean-valued CSVs still parse")
    body = "0.0,0,0\n0.000001,1,0\n0.000002,0,1\n"
    p = tmp / "headerless.csv"
    p.write_text(body, encoding="utf-8")
    c = salcap.Capture.from_csv(str(p))
    expect("synthesised channel names", c.names, ["Channel 0", "Channel 1"])
    p2 = tmp / "bools.csv"
    p2.write_text("Time,A,B\n0.0,false,true\n0.001,true,false\n", encoding="utf-8")
    c2 = salcap.Capture.from_csv(str(p2))
    expect("true/false parsed", c2.levels["A"], [0, 1])

    print("\nmalformed input fails loudly")
    bad = tmp / "bad.csv"
    bad.write_text("Time,A\n0.0,0\n0.001,banana\n", encoding="utf-8")
    try:
        salcap.Capture.from_csv(str(bad))
        expect("raises on a non-digital level", False, True)
    except ValueError as exc:
        expect("raises on a non-digital level", "banana" in str(exc), True)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        run(Path(td))
    print()
    if _failures:
        print(f"{FAIL}: {len(_failures)} check(s) failed: {', '.join(_failures)}")
        return 1
    print(f"{PASS}: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
