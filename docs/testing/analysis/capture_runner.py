#!/usr/bin/env python3
"""One test case, end to end: arm the Logic, drive the target, measure, assert.

Drives the Saleae Logic 2 automation API (https://saleae.github.io/logic2-automation/)
rather than clicking in the GUI, so a case is a command line and its result is an
exit status.  A run does, in order:

  1. connect to the Logic 2 automation server (gRPC, default 127.0.0.1:10430);
  2. start a capture with an explicit sample rate, threshold and channel set;
  3. run the SPI command on the target over ssh, capturing its output;
  4. stop the capture and export the RAW DIGITAL CSV plus a .sal for the record;
  5. label the CSV columns SCLK/MOSI/MISO/CE0/CE1;
  6. hand the CSV to analyze_capture.py with this case's expectations.

Everything lands in one directory: the capture, the export, the target's own
output, the measurements as JSON, and a run.json recording which build was
loaded.  A capture therefore cannot be separated from the build that produced it,
which is the failure mode this plan is most exposed to.

Exit status:
  0  the capture was taken and every expectation held
  1  the capture was taken and a measurement contradicted an expectation
  2  the capture could not be analysed
  3  the capture never happened (no Logic, no target, export failed)

Note that 1 is a result, not a malfunction: on a baseline build the expectations
describing the fixed behaviour are *supposed* to fail.

Examples
--------
MR 1 baseline: chip select should be dead, data should be on the bus anyway.

    capture_runner.py --out runs/mr1-b0 --label mr1-b0 \\
        --target root@up4000 \\
        --spi 'python3 spi_case.py --mode 0 --speed 1000000 --len 8' \\
        --expect-cs-edges 0 --expect-mosi aa55aa55aa55aa55

Same case against the fixed build; only the expectation changes.

    capture_runner.py --out runs/mr1-b1 --label mr1-b1 ... --expect-cs-edges 2

Rehearse the whole pipeline with no hardware at all.  --simulate replaces the
Logic with a synthetic capture, and everything downstream runs for real:

    capture_runner.py --out /tmp/rehearsal --label demo \\
        --simulate mode=0,hz=1e6,bytes=aa55aa55,cs-dead \\
        --expect-cs-edges 0 --expect-mosi aa55aa55
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CHANNELS = "MOSI=0,MISO=1,SCLK=2,CE0=3,CE1=4"


def logic_export_dir(outdir: Path) -> str:
    """Path to hand Logic 2 for its exports.

    Logic 2 writes to *its own* filesystem.  Under WSL2 the script and Logic
    share the disk through /mnt/c, but Logic is a Windows process and only
    understands C:\\-style paths, so a /mnt/c/... directory silently produces
    no export.  Translate with wslpath when one is present; otherwise the path
    is already native to whatever host Logic runs on.
    """
    p = str(outdir)
    if p.startswith("/mnt/") and os.path.exists("/usr/bin/wslpath"):
        try:
            return subprocess.run(
                ["wslpath", "-w", p], capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return p


# --------------------------------------------------------------------------
# helpers


def parse_channels(spec: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, idx = item.partition("=")
        if not _:
            raise ValueError(f"channel spec {item!r} is not NAME=INDEX")
        out[name.strip()] = int(idx)
    if "SCLK" not in out:
        raise ValueError("channel map must include SCLK")
    return out


def parse_simulate(spec: str) -> dict:
    """mode=0,hz=1e6,bytes=aa55aa55[,cs-dead][,no-loopback]"""
    kw = {"mode": 0, "hz": 1e6, "bytes": "aa55aa55", "cs_dead": False, "loopback": True}
    for item in spec.split(","):
        item = item.strip()
        if item == "cs-dead":
            kw["cs_dead"] = True
        elif item == "no-loopback":
            kw["loopback"] = False
        elif "=" in item:
            k, _, v = item.partition("=")
            k = k.strip()
            if k == "mode":
                kw["mode"] = int(v)
            elif k == "hz":
                kw["hz"] = float(v)
            elif k == "bytes":
                kw["bytes"] = v.strip()
            else:
                raise ValueError(f"unknown simulate key {k!r}")
        elif item:
            raise ValueError(f"unknown simulate flag {item!r}")
    return kw


def run_local(cmd: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def ssh_cmd(target: str, ssh_opts: str, remote: str) -> list[str]:
    return ["ssh", *shlex.split(ssh_opts), target, remote]


def collect_provenance(target: str | None, ssh_opts: str) -> dict:
    """What is actually loaded on the target right now.

    Best effort by design: a missing field is worth less than a failed run, so
    every command here is allowed to fail without stopping the capture.
    """
    prov = {"captured_at": datetime.now(timezone.utc).isoformat()}
    if not target:
        return prov
    # srcversion comes from /sys/module, never from modinfo.  modinfo reports
    # what is installed under /lib/modules, which is not necessarily what is
    # running; sysfs reports the module that is actually loaded.  And the
    # module under test is the out-of-tree spi-pxa2xx-up, not the in-tree
    # spi-pxa2xx-platform -- reading the wrong one is silent, because it
    # returns a perfectly good srcversion that simply never changes between
    # builds.  The first UP 4000 run recorded the stock value 49 times over
    # and the report claimed four distinct builds on the strength of it.
    probes = {
        "build_id": "cat /etc/up-testbuild 2>/dev/null",
        "kernel": "uname -r",
        "srcversion": "cat /sys/module/pinctrl_upboard/srcversion 2>/dev/null",
        "spi_module": (
            "for m in spi_pxa2xx_up spi_pxa2xx_platform; do "
            "[ -e /sys/module/$m/srcversion ] && echo $m; done"
        ),
        "spi_srcversion": (
            "cat /sys/module/spi_pxa2xx_up/srcversion 2>/dev/null || "
            "cat /sys/module/spi_pxa2xx_platform/srcversion 2>/dev/null"
        ),
    }
    for key, remote in probes.items():
        # A field that cannot be read is recorded as null rather than dropped:
        # a missing key reads as "not collected", which is how a provenance
        # claim nobody could check got into a report.
        prov[key] = None
        try:
            r = run_local(ssh_cmd(target, ssh_opts, remote), timeout=20)
            value = r.stdout.strip()
            if value:
                prov[key] = value
        except (OSError, subprocess.SubprocessError):
            pass
    return prov


# --------------------------------------------------------------------------
# capture back ends


def capture_with_logic(args, channels: dict[str, int], outdir: Path) -> tuple[Path, dict]:
    try:
        from saleae import automation
    except ImportError:
        raise RuntimeError(
            "the Logic 2 automation package is missing: pip install logic2-automation "
            "(and enable the automation server in Logic 2: Preferences, bottom of the page)"
        )

    indices = sorted(set(channels.values()))
    # Apply Logic 2's own capture-time glitch filter (the same one the GUI
    # exposes) rather than post-filtering in the decoder: it runs on the device,
    # matches what a human sees in the GUI, and keeps salcap.py free of any
    # filtering that could mask a real edge.  A few-nanosecond floor removes the
    # single-sample crosstalk needles a 100 MS/s capture picks up on the header
    # while staying far below a real half-period (125 ns even at 4 MHz).
    glitch_filters = []
    if args.glitch_filter and args.glitch_filter > 0:
        glitch_filters = [
            automation.GlitchFilterEntry(
                channel_index=i, pulse_width_seconds=args.glitch_filter
            )
            for i in indices
        ]
    device_config = automation.LogicDeviceConfiguration(
        enabled_digital_channels=indices,
        digital_sample_rate=args.sample_rate,
        digital_threshold_volts=args.threshold,
        glitch_filters=glitch_filters,
    )
    timed = args.seconds is not None
    capture_config = automation.CaptureConfiguration(
        capture_mode=(
            automation.TimedCaptureMode(duration_seconds=args.seconds)
            if timed
            else automation.ManualCaptureMode()
        )
    )

    target_result: dict = {}
    with automation.Manager.connect(
        address=args.logic_host, port=args.logic_port, connect_timeout_seconds=args.connect_timeout
    ) as manager:
        info = manager.get_app_info()
        with manager.start_capture(
            device_id=args.device_id,
            device_configuration=device_config,
            capture_configuration=capture_config,
        ) as capture:
            # Let the capture actually be running before the target moves a pin.
            time.sleep(args.settle)
            target_result = drive_target(args)
            if timed:
                capture.wait()
            else:
                time.sleep(args.settle)
                capture.stop()

            export_dir = logic_export_dir(outdir)
            capture.export_raw_data_csv(directory=export_dir, digital_channels=indices)
            if not args.no_sal:
                capture.save_capture(filepath=os.path.join(export_dir, "capture.sal"))

    target_result["logic"] = {
        "app_version": getattr(info, "app_version", None),
        "api_version": str(getattr(info, "api_version", "")),
        "sample_rate": args.sample_rate,
        "threshold_volts": args.threshold,
        "mode": "timed" if timed else "manual",
    }
    return find_digital_csv(outdir), target_result


def capture_simulated(args, channels: dict[str, int], outdir: Path) -> tuple[Path, dict]:
    """Fabricate the capture so the rest of the pipeline can be rehearsed."""
    sys.path.insert(0, str(HERE))
    import make_fixture

    kw = parse_simulate(args.simulate)
    rows = make_fixture.build(
        bytes.fromhex(kw["bytes"]),
        mode=kw["mode"],
        hz=kw["hz"],
        cs_dead=kw["cs_dead"],
        loopback=kw["loopback"],
    )
    path = outdir / "digital.csv"
    # Mimic a real export: columns in ascending channel-index order, named after
    # the channel rather than after what is wired to it.
    known = [n for n in make_fixture.CHANNELS if n in channels]
    names = sorted(known, key=lambda n: channels[n])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Time [s]"] + [f"Channel {channels[n]}" for n in names])
        for t, state in rows:
            w.writerow([f"{t:.12f}"] + [state[n] for n in names])
    target_result = drive_target(args)
    target_result["logic"] = {"simulated": kw}
    return path, target_result


def drive_target(args) -> dict:
    if not args.spi:
        return {"target": None}
    if args.target:
        cmd = ssh_cmd(args.target, args.ssh_opts, args.spi)
    else:
        cmd = ["sh", "-c", args.spi]
    try:
        r = run_local(cmd, timeout=args.spi_timeout)
    except subprocess.TimeoutExpired:
        return {"target": {"cmd": args.spi, "exit": None, "error": "timed out"}}
    except OSError as exc:
        return {"target": {"cmd": args.spi, "exit": None, "error": str(exc)}}
    return {
        "target": {
            "cmd": args.spi,
            "exit": r.returncode,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
        }
    }


def find_digital_csv(outdir: Path) -> Path:
    for pattern in ("digital.csv", "digital*.csv", "*.csv"):
        hits = sorted(glob.glob(str(outdir / pattern)))
        hits = [h for h in hits if not h.endswith(("analysis.json", "analog.csv"))]
        if hits:
            return Path(hits[0])
    raise RuntimeError(f"no CSV export found in {outdir}")


def label_columns(path: Path, channels: dict[str, int]) -> list[str]:
    """Rewrite the exported header as SCLK/MOSI/MISO/CE0/CE1.

    Logic 2 names the exported columns after the channel, not after what is
    wired to it.  Renaming here means every downstream tool and every human who
    opens the CSV sees the same names this plan uses.  The untouched original
    stays inside capture.sal.

    Returns the resolved column names.  Three strategies, most trustworthy
    first: names the channel map already knows (someone labelled the channels in
    the Logic 2 UI), the index embedded in a "Channel 3" style name, and finally
    position within the ascending channel list, which is the export order.
    """
    by_index = {v: k for k, v in channels.items()}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise RuntimeError(f"{path} is empty")

    header = rows[0]
    cells = [c.strip() for c in header[1:]]
    ncols = len(cells)
    ordered = sorted(by_index)

    upper = {k.upper(): k for k in channels}
    parsed: list[int | None] = []
    for cell in cells:
        digits = "".join(ch for ch in cell if ch.isdigit())
        parsed.append(int(digits) if digits else None)

    if all(c.upper() in upper for c in cells) and len(set(c.upper() for c in cells)) == ncols:
        resolved = [upper[c.upper()] for c in cells]
    elif len(set(parsed)) == ncols and all(p in by_index for p in parsed):
        resolved = [by_index[p] for p in parsed]
    elif ncols == len(ordered):
        resolved = [by_index[i] for i in ordered]
    else:
        raise RuntimeError(
            f"{path} has {ncols} data columns but the channel map names "
            f"{len(ordered)}; fix --channels"
        )

    rows[0] = [header[0]] + resolved
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return resolved


# --------------------------------------------------------------------------


def build_analysis_cmd(args, csv_path: Path, outdir: Path, resolved: list[str]) -> list[str]:
    # On a loopback bench MOSI and MISO can be a single probed node (header pins
    # 19 and 21 jumpered), so the export carries one data column.  When there is
    # no distinct MISO column, decode MISO from the MOSI column: it is the same
    # wire, which is exactly what the jumper guarantees.
    mosi_col = "MOSI" if "MOSI" in resolved else "MISO"
    miso_col = "MISO" if "MISO" in resolved else mosi_col
    cmd = [
        sys.executable,
        str(HERE / "analyze_capture.py"),
        str(csv_path),
        "--json",
        str(outdir / "analysis.json"),
        "--sclk", "SCLK", "--mosi", mosi_col, "--miso", miso_col,
    ]
    if args.cs:
        cmd += ["--cs", args.cs]
    if args.bits_per_word != 8:
        cmd += ["--bits", str(args.bits_per_word)]
    if args.assume_mode is not None:
        cmd += ["--assume-mode", str(args.assume_mode)]
    for flag, value in (
        ("--expect-cs-edges", args.expect_cs_edges),
        ("--expect-transactions", args.expect_transactions),
        ("--expect-mode", args.expect_mode),
        ("--expect-hz", args.expect_hz),
        ("--expect-mosi", args.expect_mosi),
        ("--expect-miso", args.expect_miso),
    ):
        if value is not None:
            cmd += [flag, str(value)]
    if args.expect_loopback:
        cmd += ["--expect-loopback"]
    if args.hz_tolerance is not None:
        cmd += ["--hz-tolerance", str(args.hz_tolerance)]
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", required=True, help="run directory; created if absent")
    ap.add_argument("--label", default=None, help="case name recorded in run.json")

    tgt = ap.add_argument_group("target")
    tgt.add_argument("--target", default=None, help="ssh destination, e.g. root@up4000")
    tgt.add_argument("--ssh-opts", default="-o BatchMode=yes -o ConnectTimeout=10")
    tgt.add_argument("--spi", default=None,
                     help="command that performs the transfer, run on the target")
    tgt.add_argument("--spi-timeout", type=float, default=60)

    lg = ap.add_argument_group("logic")
    lg.add_argument("--logic-host", default="127.0.0.1")
    lg.add_argument("--logic-port", type=int, default=10430)
    lg.add_argument("--connect-timeout", type=float, default=20)
    lg.add_argument("--device-id", default=None,
                    help="omit to use the only connected device; see --list-devices")
    lg.add_argument("--channels", default=DEFAULT_CHANNELS,
                    help=f"NAME=INDEX map (default {DEFAULT_CHANNELS})")
    lg.add_argument("--sample-rate", type=int, default=62_500_000,
                    help="digital samples/s; keep it >=10x the SPI clock")
    lg.add_argument("--threshold", type=float, default=1.2,
                    help="logic threshold in volts; 1.2 for 3.3V signalling")
    lg.add_argument("--glitch-filter", type=float, default=20e-9,
                    help="Logic 2 device glitch filter: drop pulses shorter than "
                         "this many seconds on every enabled channel (default 20e-9 "
                         "= 20 ns; 0 disables). Removes single-sample crosstalk "
                         "needles a 100 MS/s header capture picks up.")
    lg.add_argument("--seconds", type=float, default=None,
                    help="timed capture of this length; default is a manual "
                         "capture bracketing the target command")
    lg.add_argument("--settle", type=float, default=0.5,
                    help="pause between arming and driving, and before stopping")
    lg.add_argument("--no-sal", action="store_true", help="skip saving capture.sal")
    lg.add_argument("--list-devices", action="store_true",
                    help="print connected Logic devices and exit")
    lg.add_argument("--simulate", default=None, metavar="SPEC",
                    help="fabricate the capture instead of using the Logic, e.g. "
                         "mode=0,hz=1e6,bytes=aa55aa55,cs-dead")

    an = ap.add_argument_group("analysis")
    an.add_argument("--cs", default="CE0")
    an.add_argument("--bits", type=int, default=8, dest="bits_per_word")
    an.add_argument("--assume-mode", type=int, choices=[0, 1, 2, 3], default=None)
    an.add_argument("--expect-cs-edges", type=int)
    an.add_argument("--expect-transactions", type=int)
    an.add_argument("--expect-mode", type=int, choices=[0, 1, 2, 3])
    an.add_argument("--expect-hz", type=float)
    an.add_argument("--hz-tolerance", type=float, default=None)
    an.add_argument("--expect-mosi")
    an.add_argument("--expect-miso")
    an.add_argument("--expect-loopback", action="store_true")

    args = ap.parse_args()

    if args.list_devices:
        try:
            from saleae import automation
            with automation.Manager.connect(
                address=args.logic_host, port=args.logic_port
            ) as m:
                for d in m.get_devices():
                    print(f"{d.device_id}  {d.device_type}"
                          f"{'  (simulation)' if d.is_simulation else ''}")
        except Exception as exc:                        # noqa: BLE001 - report and exit
            print(f"error: {exc}", file=sys.stderr)
            return 3
        return 0

    try:
        channels = parse_channels(args.channels)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    prov = collect_provenance(args.target, args.ssh_opts)
    try:
        if args.simulate:
            csv_path, result = capture_simulated(args, channels, outdir)
        else:
            csv_path, result = capture_with_logic(args, channels, outdir)
        resolved = label_columns(csv_path, channels)
    except Exception as exc:                            # noqa: BLE001 - report and exit
        print(f"error: {exc}", file=sys.stderr)
        (outdir / "run.json").write_text(
            json.dumps({"label": args.label, "error": str(exc), **prov}, indent=2),
            encoding="utf-8",
        )
        return 3

    run = {
        "label": args.label,
        "channels": channels,
        "csv_columns": resolved,
        "capture_csv": str(csv_path),
        **prov,
        **result,
    }
    (outdir / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")

    tr = result.get("target")
    if tr:
        print(f"target       exit {tr.get('exit')}  {tr.get('cmd')}")
        for line in (tr.get("stdout") or "").splitlines():
            print(f"  | {line}")
    if prov.get("build_id"):
        print(f"build        {prov['build_id']}")
    print(f"run          {outdir / 'run.json'}")

    rc = subprocess.run(build_analysis_cmd(args, csv_path, outdir, resolved)).returncode
    return rc


if __name__ == "__main__":
    sys.exit(main())
