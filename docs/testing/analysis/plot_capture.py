#!/usr/bin/env python3
"""Draw a capture as an annotated waveform figure, for an issue or an MR.

The Logic 2 automation API exports data — CSV, binary, .sal — and nothing
graphical.  There is no screenshot, image or viewport call in it, so a figure
for a bug report has to be rendered here.  Output is SVG: no dependencies, no
rasterisation, crisp at any zoom, and small enough to commit next to the CSV it
came from.

The annotations are the *measured* values from salcap, the same numbers
analyze_capture.py asserts on.  The picture and the claim therefore cannot
drift apart: if the figure says mode 0 at 1 MHz, that is what the decoder saw,
not a caption someone typed.

    # one capture
    plot_capture.py mr1-b0/digital.csv --out mr1-b0.svg --cs CE0

    # the pair that makes the case, stacked in one figure
    plot_capture.py mr1-b0/digital.csv mr1-b1/digital.csv --out mr1.svg \\
        --cs CE0 --labels "master" "with fix" \\
        --title "UP 4000: chip select across an 8-byte transfer"

GitHub renders SVG committed to a repository (the same path badges use), so
link the committed file from the issue body.  For a drag-and-drop attachment,
add --png and it will convert with whatever is installed.
"""

from __future__ import annotations

import argparse
import html
import os
import shutil
import struct
import subprocess
import sys
import zlib

import salcap

# Presentation attributes only, never a <style> block: GitHub sanitises SVG and
# a stripped stylesheet would leave an unreadable figure.
BG = "#ffffff"
INK = "#111827"
MUTED = "#6b7280"
GRID = "#e5e7eb"
SHADE = "#dbeafe"
ACCENT = {"before": "#b45309", "after": "#047857", None: "#374151"}
TRACE = {
    "SCLK": "#2563eb",
    "MOSI": "#7c3aed",
    "MISO": "#0891b2",
    "CE0": "#b45309",
    "CE1": "#a16207",
}
FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

ROW_H = 34          # per-channel row height
ROW_GAP = 8
PAD_L = 74          # room for channel labels
PAD_R = 18
HEAD_H = 68         # panel header: label, measurements, and the shading caption
AXIS_H = 26


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def fmt_hz(v) -> str:
    # Thresholds are nudged below the round number so a measured 999_999.7 Hz
    # prints as 1.000 MHz rather than 1000.0 kHz.  Two panels being compared
    # should not disagree about units because of float noise.
    if v is None:
        return "n/a"
    if v >= 999_500:
        return f"{v/1e6:.3f} MHz"
    if v >= 999.5:
        return f"{v/1e3:.1f} kHz"
    return f"{v:.0f} Hz"


def fmt_time(t: float) -> str:
    if t >= 1e-3:
        return f"{t*1e3:.2f} ms"
    if t >= 1e-6:
        return f"{t*1e6:.1f} us"
    return f"{t*1e9:.0f} ns"


def nice_ticks(lo: float, hi: float, target: int = 6) -> list[float]:
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / target
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    for mult in (1, 2, 2.5, 5, 10):
        step = mag * mult
        if span / step <= target:
            break
    first = (int(lo / step) + (1 if lo > 0 else 0)) * step
    ticks, t = [], first
    while t <= hi + step * 1e-9:
        ticks.append(t)
        t += step
    return ticks


def changes_in(cap: salcap.Capture, chan: str, lo: float, hi: float):
    """(time, level) at `lo`, then every transition inside the window.

    Built from transitions rather than rows, so a uniformly sampled export
    plots as compactly as a change-based one.
    """
    name = cap.resolve(chan)
    pts = [(lo, cap.level_at(name, lo))]
    for t, lv in cap.edges(name):
        if lo < t <= hi:
            pts.append((t, lv))
    return pts


class Panel:
    """One capture, measured and laid out."""

    def __init__(self, path, sclk, mosi, miso, cs, label=None, accent=None,
                 bits_per_word=8):
        self.cap = salcap.Capture.from_csv(path)
        self.result = salcap.analyse(
            self.cap, sclk=sclk, mosi=mosi, miso=miso, cs=cs,
            bits_per_word=bits_per_word,
        )
        self.label = label or os.path.basename(path)
        self.accent = accent
        # Jumpered MOSI/MISO resolve to one column; drawing it twice under two
        # labels invents a second trace that was never probed.
        self.channels = []
        for c in (sclk, mosi, miso, cs):
            if c and self.cap.resolve(c) not in {
                self.cap.resolve(x) for x in self.channels
            }:
                self.channels.append(c)
        self.cs = cs

    def auto_window(self, pad=0.25):
        tx = self.result["transactions"]
        if not tx:
            return self.cap.times[0], self.cap.times[-1]
        lo, hi = tx[0]["start_s"], tx[-1]["end_s"]
        margin = max((hi - lo) * pad, 1e-6)
        return max(lo - margin, self.cap.times[0]), min(hi + margin, self.cap.times[-1])

    def caption(self) -> list[str]:
        s = self.result["summary"]
        mode = s.get("mode_measured")
        if s.get("frame_truncated"):
            # CPOL is read from the level after the last clock edge in the
            # frame.  When the frame closed early that level belongs to a
            # transfer still in progress, so there is no idle level to read.
            bits = ["mode not measurable (frame torn)"]
        else:
            bits = [
                f"mode {mode if mode is not None else '?'}"
                f" (cpol={s.get('cpol_measured')} cpha={s.get('cpha_measured')})"
            ]
        bits.append(fmt_hz(s.get("freq_hz")))
        if "cs_edges" in self.result:
            n = self.result["cs_edges"]
            note = ""
            if n == 0:
                lvl = self.result.get("cs_constant_level")
                note = (
                    " — stuck asserted (low)" if lvl == 0
                    else " — never asserts (high)" if lvl == 1
                    else " — never moves"
                )
            bits.append(f"CS edges {n}{note}")
        if s.get("frame_truncated"):
            bits.append(
                f"{s['clock_edges_after_frame']} clock edges AFTER CS released"
            )
        line2 = []
        if s.get("mosi_hex"):
            line2.append(f"MOSI {s['mosi_hex']}")
        # A shared MOSI/MISO node decodes to one column, so printing both would
        # show the same bytes twice and read as corroboration.
        if s.get("miso_hex") and not s.get("loopback_shared_channel"):
            line2.append(f"MISO {s['miso_hex']}")
        if s.get("loopback_match") is not None:
            line2.append("loopback " + ("match" if s["loopback_match"] else "MISMATCH"))
        elif s.get("loopback_shared_channel"):
            line2.append("MOSI/MISO jumpered — one probed node")
        out = ["   ".join(bits)]
        if line2:
            out.append("   ".join(line2))
        return out

    def height(self) -> int:
        return HEAD_H + len(self.channels) * (ROW_H + ROW_GAP) + AXIS_H

    def render(self, x0: int, y0: int, width: int, lo: float, hi: float) -> list[str]:
        out: list[str] = []
        plot_w = width - PAD_L - PAD_R
        accent = ACCENT.get(self.accent, ACCENT[None])

        def sx(t: float) -> float:
            return x0 + PAD_L + (t - lo) / (hi - lo) * plot_w

        out.append(
            f'<rect x="{x0+2}" y="{y0}" width="4" height="{self.height()-6}" '
            f'fill="{accent}" rx="2"/>'
        )
        out.append(
            f'<text x="{x0+PAD_L}" y="{y0+16}" font-family="{FONT}" font-size="14" '
            f'font-weight="700" fill="{INK}">{esc(self.label)}</text>'
        )
        for i, line in enumerate(self.caption()):
            out.append(
                f'<text x="{x0+PAD_L}" y="{y0+34+i*15}" font-family="{FONT}" '
                f'font-size="12" fill="{MUTED}">{esc(line)}</text>'
            )

        top = y0 + HEAD_H
        rows_h = len(self.channels) * (ROW_H + ROW_GAP)

        # Chip-select assert windows, shaded behind every trace.
        if self.cs and self.result.get("cs_edges"):
            for i, w in enumerate(salcap.cs_windows(self.cap, self.cap.resolve(self.cs))):
                a, b = max(w.start, lo), min(w.end, hi)
                if b <= a:
                    continue
                out.append(
                    f'<rect x="{sx(a):.2f}" y="{top}" width="{sx(b)-sx(a):.2f}" '
                    f'height="{rows_h}" fill="{SHADE}" opacity="0.55"/>'
                )
                if i == 0 and sx(b) - sx(a) > 120:
                    out.append(
                        f'<text x="{(sx(a)+sx(b))/2:.2f}" y="{top-5}" '
                        f'text-anchor="middle" font-family="{FONT}" font-size="10" '
                        f'fill="{MUTED}">{esc(self.cs)} asserted, '
                        f'{esc(fmt_time(w.end - w.start))}</text>'
                    )

        for t in nice_ticks(lo, hi):
            out.append(
                f'<line x1="{sx(t):.2f}" y1="{top}" x2="{sx(t):.2f}" '
                f'y2="{top+rows_h}" stroke="{GRID}" stroke-width="1"/>'
            )

        for row, chan in enumerate(self.channels):
            ry = top + row * (ROW_H + ROW_GAP)
            hi_y, lo_y = ry + 5, ry + ROW_H - 5
            colour = TRACE.get(chan.upper(), "#374151")

            out.append(
                f'<text x="{x0+PAD_L-10}" y="{lo_y}" text-anchor="end" '
                f'font-family="{FONT}" font-size="12" fill="{INK}">{esc(chan)}</text>'
            )

            pts = changes_in(self.cap, chan, lo, hi)
            coords, prev = [], pts[0][1]
            for t, lv in pts:
                y_prev = hi_y if prev else lo_y
                y_new = hi_y if lv else lo_y
                coords.append(f"{sx(t):.2f},{y_prev:.2f}")
                if lv != prev:
                    coords.append(f"{sx(t):.2f},{y_new:.2f}")
                prev = lv
            coords.append(f"{sx(hi):.2f},{(hi_y if prev else lo_y):.2f}")
            out.append(
                f'<polyline points="{" ".join(coords)}" fill="none" '
                f'stroke="{colour}" stroke-width="1.8" stroke-linejoin="miter"/>'
            )

            if len(pts) == 1:
                out.append(
                    f'<text x="{sx(lo)+8:.2f}" y="{ry+ROW_H/2+4:.2f}" '
                    f'font-family="{FONT}" font-size="11" fill="{MUTED}">'
                    f'no transitions in window</text>'
                )

        axis_y = top + rows_h + 14
        out.append(
            f'<line x1="{sx(lo):.2f}" y1="{axis_y-8}" x2="{sx(hi):.2f}" '
            f'y2="{axis_y-8}" stroke="{MUTED}" stroke-width="1"/>'
        )
        for t in nice_ticks(lo, hi):
            out.append(
                f'<text x="{sx(t):.2f}" y="{axis_y+6}" text-anchor="middle" '
                f'font-family="{FONT}" font-size="10" fill="{MUTED}">'
                f'{esc(fmt_time(t - lo))}</text>'
            )
        return out


def build_svg(panels, width, title=None, footer=None) -> tuple[str, int]:
    lo = min(p.auto_window()[0] for p in panels)
    hi = max(p.auto_window()[1] for p in panels)
    if hi <= lo:
        hi = lo + 1e-6

    head = 34 if title else 8
    foot = 20 if footer else 8
    height = head + sum(p.height() + 10 for p in panels) + foot

    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{esc(title or "SPI capture")}">',
        f'<rect width="{width}" height="{height}" fill="{BG}"/>',
    ]
    if title:
        body.append(
            f'<text x="14" y="24" font-family="{FONT}" font-size="15" '
            f'font-weight="700" fill="{INK}">{esc(title)}</text>'
        )

    y = head
    for p in panels:
        body += p.render(0, y, width, lo, hi)
        y += p.height() + 10

    if footer:
        body.append(
            f'<text x="14" y="{height-7}" font-family="{FONT}" font-size="10" '
            f'fill="{MUTED}">{esc(footer)}</text>'
        )
    body.append("</svg>")
    return "\n".join(body) + "\n", height


def _png_trim_rows(path: str, keep: int) -> None:
    """Truncate a PNG to its first `keep` rows, in place.

    Pure stdlib.  Needed only for the browser backend: a headless screenshot
    has to be given viewport headroom or it renders short, and the headroom
    then shows up as blank space under the figure.
    """
    data = open(path, "rb").read()
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        if typ == b"IHDR":
            ihdr = data[pos + 8:pos + 8 + ln]
        elif typ == b"IDAT":
            idat += data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
    if ihdr is None:
        raise RuntimeError(f"{path}: no IHDR")

    w, h, depth, colour = struct.unpack(">IIBB", ihdr[:10])
    if depth != 8 or colour not in (2, 6) or keep >= h:
        return
    nchan = 3 if colour == 2 else 4
    stride = w * nchan

    raw = zlib.decompress(idat)
    out, prev, i = bytearray(), bytearray(stride), 0
    for _ in range(keep):
        ftype = raw[i]; i += 1
        line = bytearray(raw[i:i + stride]); i += stride
        for x in range(stride):
            a = line[x - nchan] if x >= nchan else 0
            b = prev[x]
            c = prev[x - nchan] if x >= nchan else 0
            if ftype == 1:
                line[x] = (line[x] + a) & 255
            elif ftype == 2:
                line[x] = (line[x] + b) & 255
            elif ftype == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if (pa <= pb and pa <= pc)
                                      else (b if pb <= pc else c))) & 255
        out += b"\x00" + line          # re-emit with filter type 0
        prev = line

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    new_ihdr = struct.pack(">II", w, keep) + ihdr[8:]
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", new_ihdr))
        fh.write(chunk(b"IDAT", zlib.compress(bytes(out), 9)))
        fh.write(chunk(b"IEND", b""))


def to_png(svg_path: str, png_path: str, width: int, height: int, scale: int = 2) -> str:
    """Convert with whatever is installed.  Returns the tool used.

    Every backend is given the figure's own dimensions, so the PNG is the
    figure and not the figure adrift in a page-sized canvas.
    """
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-w", str(width * scale),
                        "-h", str(height * scale), "-o", png_path, svg_path],
                       check=True)
        return "rsvg-convert"
    try:
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=png_path,
                         output_width=width * scale, output_height=height * scale)
        return "cairosvg"
    except ImportError:
        pass
    if shutil.which("inkscape"):
        subprocess.run(["inkscape", svg_path, "--export-type=png",
                        f"--export-filename={png_path}",
                        f"--export-width={width*scale}"], check=True)
        return "inkscape"
    for magick in ("magick", "convert"):
        if shutil.which(magick):
            args = [magick] if magick == "convert" else [magick, "convert"]
            subprocess.run(args + ["-density", str(96 * scale), svg_path, png_path],
                           check=True)
            return magick
    for chrome in ("chromium", "chromium-browser", "google-chrome", "chrome",
                   "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                   r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        exe = shutil.which(chrome) or (chrome if os.path.exists(chrome) else None)
        if exe:
            # A standalone SVG document gets sized against the viewport and the
            # default body margin, which crops the figure.  Wrap it in a
            # zero-margin page at its natural size so the screenshot is exact.
            wrapper = svg_path + ".shot.html"
            # An <img> is a replaced element at exactly the CSS size given.
            # An inline <svg> is not: the browser sizes it against the viewport
            # and silently scales the figure down, which is how the first
            # version of this cropped the bottom panel.
            with open(wrapper, "w", encoding="utf-8") as fh:
                fh.write(
                    "<!doctype html><meta charset='utf-8'><style>"
                    "html,body{margin:0;padding:0;background:#fff}"
                    "img{display:block}</style>"
                    f"<body><img src='{os.path.basename(svg_path)}' "
                    f"width='{width}' height='{height}'>"
                )
            try:
                # Headroom: asked for exactly `height`, the screenshot comes
                # back with the figure truncated.  Over-request, then trim.
                subprocess.run([exe, "--headless", "--disable-gpu", "--no-sandbox",
                                f"--screenshot={png_path}",
                                f"--window-size={width},{height + 200}",
                                "--hide-scrollbars",
                                "--default-background-color=FFFFFFFF",
                                f"--force-device-scale-factor={scale}",
                                f"file://{os.path.abspath(wrapper)}"],
                               check=True, capture_output=True)
            finally:
                os.unlink(wrapper)
            _png_trim_rows(png_path, height * scale)
            return os.path.basename(exe)
    raise RuntimeError(
        "no SVG converter found; install one of rsvg-convert, cairosvg, "
        "inkscape or ImageMagick, or just use the SVG"
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("csv", nargs="+", help="one capture, or two to stack them")
    ap.add_argument("--out", required=True, help="output .svg path")
    ap.add_argument("--png", metavar="PATH", help="also write a PNG, if a converter exists")
    ap.add_argument("--labels", nargs="*", default=None, help="one label per capture")
    ap.add_argument("--title", default=None)
    ap.add_argument("--footer", default=None,
                    help="provenance line, e.g. the build ids the captures came from")
    ap.add_argument("--width", type=int, default=1000)
    ap.add_argument("--sclk", default="SCLK")
    ap.add_argument("--mosi", default="MOSI")
    ap.add_argument("--miso", default="MISO")
    ap.add_argument("--cs", default=None)
    ap.add_argument("--bits", type=int, default=8, dest="bits_per_word")
    ap.add_argument("--window", nargs=2, type=float, metavar=("START", "END"),
                    default=None, help="time window in seconds; default auto-fits "
                                       "the transactions")
    args = ap.parse_args()

    if len(args.csv) > 2:
        print("error: at most two captures", file=sys.stderr)
        return 2

    labels = args.labels or ([] if len(args.csv) == 1 else ["before", "after"])
    accents = ["before", "after"] if len(args.csv) == 2 else [None]

    try:
        panels = [
            Panel(path, args.sclk, args.mosi, args.miso, args.cs,
                  label=labels[i] if i < len(labels) else None,
                  accent=accents[i] if i < len(accents) else None,
                  bits_per_word=args.bits_per_word)
            for i, path in enumerate(args.csv)
        ]
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.window:
        for p in panels:
            p.auto_window = lambda _p=p, w=args.window: (w[0], w[1])

    svg, height = build_svg(panels, args.width, args.title, args.footer)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"wrote {args.out}  ({args.width}x{height}, {len(svg)/1024:.1f} kB)")
    for p in panels:
        print(f"  {p.label}: {'; '.join(p.caption())}")

    if args.png:
        try:
            tool = to_png(args.out, args.png, args.width, height)
            print(f"wrote {args.png}  (via {tool})")
        except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
            print(f"warning: PNG conversion failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
