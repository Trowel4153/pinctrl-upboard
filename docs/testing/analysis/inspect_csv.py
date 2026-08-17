#!/usr/bin/env python3
"""Dump exactly what the analysis pipeline sees in a raw Logic CSV export.

Usage: inspect_csv.py <digital.csv>

Prints the header, the first and last rows verbatim, the set of distinct values
in each channel column, total row count / time span, and a naive element-wise
transition count per channel.  No decoding, no assumptions — just what is on
disk, so a wrong number here localises the problem to the export/parse, and a
right number here pushes it downstream into the decoder.
"""
import csv, sys, collections

path = sys.argv[1]
with open(path, newline="", encoding="utf-8-sig") as fh:
    rows = list(csv.reader(fh))

hdr = rows[0]
data = rows[1:]
cols = hdr[1:]
print(f"file        {path}")
print(f"header      {hdr}")
print(f"rows        {len(data)}")

# time span
try:
    t0 = float(data[0][0]); t1 = float(data[-1][0])
    print(f"time span   {t0:.9f} .. {t1:.9f}  ({t1 - t0:.6f} s)")
except (ValueError, IndexError):
    print("time span   <first column is not a float time?>")

print("\nfirst 12 rows:")
for r in data[:12]:
    print("  ", r)
print("last 4 rows:")
for r in data[-4:]:
    print("  ", r)

print("\ndistinct values per channel column:")
for i, c in enumerate(cols, start=1):
    vals = collections.Counter(r[i] for r in data if len(r) > i)
    # show at most 6 distinct tokens
    shown = dict(list(vals.items())[:6])
    print(f"  {c:6s} {dict(shown)}  (n_distinct={len(vals)})")

print("\nnaive element-wise transitions per channel (consecutive rows differ):")
prev = None
tr = [0] * len(cols)
for r in data:
    v = r[1:]
    if prev is not None:
        for i in range(min(len(v), len(prev))):
            if v[i] != prev[i]:
                tr[i] += 1
    prev = v
for c, n in zip(cols, tr):
    print(f"  {c:6s} {n}")
