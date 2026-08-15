"""Measure SPI behaviour from a Saleae Logic 2 **raw digital** CSV export.

Deliberately does not parse Logic 2's *analyzer* CSV export.  Two reasons:

  * the analyzer export schema differs between Logic 2 releases, while the raw
    digital export has been a stable "time, then one column per channel" table;
  * decoding here means we can decode under an *assumed* mode and compare that
    against the mode actually present on the wire, which is the whole point of
    the SSCR latch test.  An analyzer export has already committed to one mode.

Export format expected: raw digital data as CSV.  One time column first, then
one column per channel.  Both the change-based export (a row whenever any
channel changes) and a uniformly sampled export parse the same way; levels are
read as 0/1, "true"/"false" or numeric.
"""

from __future__ import annotations

import bisect
import csv
import statistics
from dataclasses import dataclass, field

_TRUE = {"1", "true", "t", "high", "h"}
_FALSE = {"0", "false", "f", "low", "l"}


def _to_bit(text: str) -> int:
    s = text.strip().lower()
    if s in _TRUE:
        return 1
    if s in _FALSE:
        return 0
    try:
        return 1 if float(s) >= 0.5 else 0
    except ValueError:
        raise ValueError(f"not a digital level: {text!r}") from None


@dataclass
class Capture:
    """A digital capture as a change list."""

    times: list[float]
    levels: dict[str, list[int]]
    source: str = ""

    @property
    def names(self) -> list[str]:
        return list(self.levels)

    @property
    def duration(self) -> float:
        return (self.times[-1] - self.times[0]) if self.times else 0.0

    @classmethod
    def from_csv(cls, path: str) -> "Capture":
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        if not rows:
            raise ValueError(f"{path}: empty file")

        header = [h.strip() for h in rows[0]]
        # A header row is one whose first cell is not a number.
        try:
            float(header[0])
        except ValueError:
            body = rows[1:]
        else:
            header = ["Time"] + [f"Channel {i}" for i in range(len(rows[0]) - 1)]
            body = rows

        if len(header) < 2:
            raise ValueError(f"{path}: need a time column plus at least one channel")

        chan_names = header[1:]
        times: list[float] = []
        levels: dict[str, list[int]] = {n: [] for n in chan_names}
        for lineno, row in enumerate(body, start=2):
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) != len(header):
                raise ValueError(
                    f"{path}:{lineno}: {len(row)} fields, header has {len(header)}"
                )
            try:
                times.append(float(row[0]))
                for name, cell in zip(chan_names, row[1:]):
                    levels[name].append(_to_bit(cell))
            except ValueError as exc:
                raise ValueError(f"{path}:{lineno}: {exc}") from None

        if not times:
            raise ValueError(f"{path}: no data rows")
        return cls(times=times, levels=levels, source=path)

    # -- channel access ----------------------------------------------------

    def resolve(self, want: str) -> str:
        """Map a user-supplied channel name onto a real column.

        Accepts the exact header text, a case-insensitive match, a bare index
        ("3"), or the common short forms ("D3", "Channel 3").
        """
        if want in self.levels:
            return want
        low = {n.lower().replace(" ", ""): n for n in self.levels}
        key = want.lower().replace(" ", "")
        if key in low:
            return low[key]
        digits = key.lstrip("dch").lstrip("annel")
        if digits.isdigit():
            idx = int(digits)
            for cand in (f"channel{idx}", f"d{idx}"):
                if cand in low:
                    return low[cand]
            if idx < len(self.levels):
                return self.names[idx]
        raise KeyError(f"no channel matching {want!r}; have {self.names}")

    def edges(self, chan: str) -> list[tuple[float, int]]:
        """(time, new_level) for every transition, in order."""
        name = self.resolve(chan)
        seq = self.levels[name]
        out = []
        for i in range(1, len(seq)):
            if seq[i] != seq[i - 1]:
                out.append((self.times[i], seq[i]))
        return out

    def level_at(self, chan: str, t: float) -> int:
        name = self.resolve(chan)
        idx = bisect.bisect_right(self.times, t) - 1
        if idx < 0:
            idx = 0
        return self.levels[name][idx]


# -- windowing -------------------------------------------------------------


@dataclass
class Window:
    start: float
    end: float

    def contains(self, t: float) -> bool:
        return self.start <= t <= self.end


def cs_windows(cap: Capture, cs: str, active_low: bool = True) -> list[Window]:
    """Assert/deassert windows for a chip select line."""
    asserted = 0 if active_low else 1
    edges = cap.edges(cs)
    out: list[Window] = []
    start = cap.times[0] if cap.level_at(cs, cap.times[0]) == asserted else None
    for t, level in edges:
        if level == asserted and start is None:
            start = t
        elif level != asserted and start is not None:
            out.append(Window(start, t))
            start = None
    if start is not None:
        out.append(Window(start, cap.times[-1]))
    return out


def clock_bursts(edge_times: list[float], gap_factor: float = 4.0) -> list[Window]:
    """Group clock edges into bursts separated by an unusually long gap.

    Used when there is no usable chip select — which is precisely the MR 1
    baseline, where the absence of CS activity is the defect under test.
    """
    if not edge_times:
        return []
    if len(edge_times) < 3:
        return [Window(edge_times[0], edge_times[-1])]
    gaps = [b - a for a, b in zip(edge_times, edge_times[1:])]
    typical = statistics.median(gaps)
    limit = typical * gap_factor
    out: list[Window] = []
    start = prev = edge_times[0]
    for t in edge_times[1:]:
        if t - prev > limit:
            out.append(Window(start, prev))
            start = t
        prev = t
    out.append(Window(start, prev))
    return out


# -- measurement -----------------------------------------------------------

# (cpol, cpha) -> level of the SCLK edge on which the data line is sampled.
# CPHA=0 samples the leading edge, CPHA=1 the trailing edge; "leading" is
# rising when CPOL=0 and falling when CPOL=1.
_SAMPLE_EDGE = {(0, 0): 1, (0, 1): 0, (1, 0): 0, (1, 1): 1}


def clock_stats(cap: Capture, sclk: str, window: Window) -> dict:
    """Idle level, period and frequency for the clock inside one window."""
    edges = [(t, lv) for t, lv in cap.edges(sclk) if window.contains(t)]
    rising = [t for t, lv in edges if lv == 1]
    periods = [b - a for a, b in zip(rising, rising[1:])]

    idle_before = 1 - edges[0][1] if edges else cap.level_at(sclk, window.start)
    idle_after = edges[-1][1] if edges else idle_before

    stats = {
        "edges": len(edges),
        "cycles": max(len(rising) - 1, 0),
        "idle_level_before": idle_before,
        "idle_level_after": idle_after,
        "idle_consistent": idle_before == idle_after,
        "cpol_measured": idle_before,
    }
    if periods:
        median = statistics.median(periods)
        stats.update(
            period_s=median,
            freq_hz=(1.0 / median) if median else None,
            period_jitter_s=(max(periods) - min(periods)),
        )
    else:
        stats.update(period_s=None, freq_hz=None, period_jitter_s=None)
    return stats


def infer_cpha(cap: Capture, sclk: str, data: str, window: Window, cpol: int) -> dict:
    """Decide whether the data line changes on leading or trailing clock edges.

    CPHA=0 launches data on the trailing edge, CPHA=1 on the leading edge.
    Needs a data pattern that actually transitions — 0xAA/0x55 is ideal, a run
    of 0x00 or 0xFF tells us nothing.
    """
    leading = 1 if cpol == 0 else 0
    clk = [(t, lv) for t, lv in cap.edges(sclk) if window.contains(t)]
    dat = [t for t, _ in cap.edges(data) if window.contains(t)]
    if not clk or not dat:
        return {"cpha_measured": None, "confidence": 0.0, "samples": 0}

    times = [t for t, _ in clk]
    near_leading = near_trailing = 0
    for t in dat:
        i = bisect.bisect_left(times, t)
        cands = [c for c in (i - 1, i, i + 1) if 0 <= c < len(clk)]
        best = min(cands, key=lambda c: abs(times[c] - t))
        if clk[best][1] == leading:
            near_leading += 1
        else:
            near_trailing += 1

    total = near_leading + near_trailing
    cpha = 1 if near_leading > near_trailing else 0
    conf = max(near_leading, near_trailing) / total if total else 0.0
    return {"cpha_measured": cpha, "confidence": round(conf, 3), "samples": total}


def decode(
    cap: Capture,
    sclk: str,
    data: str,
    window: Window,
    cpol: int,
    cpha: int,
    bits_per_word: int = 8,
    msb_first: bool = True,
    backoff_fraction: float = 0.25,
) -> dict:
    """Sample `data` on the clock edge implied by (cpol, cpha) and group to words."""
    want = _SAMPLE_EDGE[(cpol, cpha)]
    edges = [(t, lv) for t, lv in cap.edges(sclk) if window.contains(t)]
    sample_times = [t for t, lv in edges if lv == want]

    rising = [t for t, lv in edges if lv == 1]
    periods = [b - a for a, b in zip(rising, rising[1:])]
    backoff = statistics.median(periods) * backoff_fraction if periods else 0.0

    bits = [cap.level_at(data, max(t - backoff, cap.times[0])) for t in sample_times]

    words: list[int] = []
    for i in range(0, len(bits) - bits_per_word + 1, bits_per_word):
        chunk = bits[i : i + bits_per_word]
        if not msb_first:
            chunk = list(reversed(chunk))
        value = 0
        for b in chunk:
            value = (value << 1) | b
        words.append(value)

    return {
        "bits": len(bits),
        "words": words,
        "hex": "".join(f"{w:02x}" for w in words),
        "leftover_bits": len(bits) % bits_per_word,
    }


def analyse(
    cap: Capture,
    sclk: str,
    mosi: str | None = None,
    miso: str | None = None,
    cs: str | None = None,
    assume_mode: int | None = None,
    bits_per_word: int = 8,
) -> dict:
    """Full measurement pass over one capture.

    Windows come from CS when a CS channel is given *and it actually toggles*;
    otherwise they are recovered from clock bursts, so a capture with a dead
    chip select still yields decoded data.
    """
    result: dict = {
        "source": cap.source,
        "duration_s": cap.duration,
        "edge_counts": {n: len(cap.edges(n)) for n in cap.names},
    }

    windows: list[Window] = []
    if cs is not None:
        cs_name = cap.resolve(cs)
        cs_edges = cap.edges(cs_name)
        result["cs_channel"] = cs_name
        result["cs_edges"] = len(cs_edges)
        result["cs_asserted"] = len(cs_edges) > 0
        windows = cs_windows(cap, cs_name)
        result["cs_windows"] = [
            {"start_s": w.start, "end_s": w.end, "duration_s": w.end - w.start}
            for w in windows
        ]
        result["framing"] = "chip-select"

    if not windows:
        windows = clock_bursts([t for t, _ in cap.edges(sclk)])
        result["framing"] = "clock-burst"
        result["burst_count"] = len(windows)

    result["transactions"] = []
    for w in windows:
        clk = clock_stats(cap, sclk, w)
        cpol = clk["cpol_measured"]
        entry: dict = {"start_s": w.start, "end_s": w.end, "clock": clk}

        ref = mosi or miso
        if ref is not None:
            cpha_info = infer_cpha(cap, sclk, ref, w, cpol)
            entry["phase"] = cpha_info
            cpha = cpha_info["cpha_measured"]
            entry["mode_measured"] = (
                (cpol << 1) | cpha if cpha is not None else None
            )
            use_cpol, use_cpha = cpol, (cpha if cpha is not None else 0)
            if assume_mode is not None:
                use_cpol, use_cpha = assume_mode >> 1, assume_mode & 1
                entry["decoded_with_mode"] = assume_mode
            else:
                entry["decoded_with_mode"] = (use_cpol << 1) | use_cpha

            if mosi is not None:
                entry["mosi"] = decode(
                    cap, sclk, mosi, w, use_cpol, use_cpha, bits_per_word
                )
            if miso is not None:
                entry["miso"] = decode(
                    cap, sclk, miso, w, use_cpol, use_cpha, bits_per_word
                )
            if mosi is not None and miso is not None:
                entry["loopback_match"] = entry["mosi"]["hex"] == entry["miso"]["hex"]

        result["transactions"].append(entry)

    if result["transactions"]:
        first = result["transactions"][0]
        result["summary"] = {
            "transactions": len(result["transactions"]),
            "mode_measured": first.get("mode_measured"),
            "cpol_measured": first["clock"]["cpol_measured"],
            "cpha_measured": first.get("phase", {}).get("cpha_measured"),
            "freq_hz": first["clock"]["freq_hz"],
            "mosi_hex": first.get("mosi", {}).get("hex"),
            "miso_hex": first.get("miso", {}).get("hex"),
            "loopback_match": first.get("loopback_match"),
        }
    else:
        result["summary"] = {"transactions": 0}
    return result
