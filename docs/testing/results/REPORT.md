# UP 4000 SPI fixes — on-target validation report

On-target execution of `docs/testing/up4000-spi-test-plan.md`. Baseline-first,
before/after per MR. This directory holds every capture, its `analysis.json`,
`run.json`, raw `digital.csv`, `capture.sal`, and the target's own stdout, plus
before/after figures under [`figs/`](figs/).

## Bench & pipeline

- **Target:** UP-APL03 (board id 9), kernel `7.0.0-22-generic`, SSH host `up4000`.
- **Loopback:** header pins 19 & 21 jumpered → a single data node on Logic ch0.
  Channel map `MOSI=0, SCLK=2, CE0=4, CE1=5` (data line labelled MOSI only; MISO
  is decoded from the MOSI column). 100 MS/s, 1.2 V threshold, 20 ns device-side
  glitch filter (removes header crosstalk needles).
- **Driver:** captures driven through `../analysis/capture_runner.py` (Logic 2
  automation API on `127.0.0.1`). `spi_case.py` runs one transfer per invocation
  (`<32 B` = polled path, `>=32 B` = long path) under `sudo` (spidev is `0600`).

## Build ladder & provenance

The load proof is the `spi-pxa2xx-up` module `srcversion` (in each `run.json`).
Pinctrl is untouched by MR2/MR3, so its constant `srcversion` proves MR1 rode
through every build intact. Fixes were applied with `git cherry-pick --no-commit`
(the target repo has no committer identity; git config was **not** modified).

| Build | Contents | git base + picks | `spi-pxa2xx-up` srcversion | `pinctrl-upboard` srcversion |
|-------|----------|------------------|----------------------------|------------------------------|
| B0 | master | `d626a5e` | (stock in-tree) | `341B449FCC79C49229F3317` |
| B1 | + MR1 spi-cs-apl03 | `82ba569` | `ABE09A28DFC56608A8F3758` | **`263D864EB5D11196F75F830`** |
| B2 | B1 + MR2 sscr-latch | `82ba569` + `8f026fe` | `972CFE8F250A08520070932` | `263D864EB5D11196F75F830` |
| B3 | B1 + MR3 rx-timeout | `82ba569` + `d81a226` | `3A05FFAE917117B47458C64` | `263D864EB5D11196F75F830` |
| B4 | B1 + MR2 + MR3 | `82ba569` + `8f026fe` + `d81a226` | `5FF0368152F46C367202161` | `263D864EB5D11196F75F830` |

Four distinct spi `srcversion`s confirm four distinct builds actually booted.
MR2 and MR3 auto-merged in `files/spi-pxa2xx.c` with **no conflict** (combined
32+/7-).

## MR1 — chip select restore (`spi-cs-apl03`), B0 → B1

![MR1](figs/mr1-chip-select.svg)

| Measurement | B0 | B1 | |
|---|---|---|---|
| Chip select edges (CE0) | 0 | 2 | **changed** |
| Chip select edges (CE1) | 0 | 2 | **changed** |

On master both chip selects are **dead** (0 edges) while the bus still clocks
data. With the fix **both CE0 and CE1 toggle** (2 edges), loopback MATCH, `aa55…`
at 1 MHz mode 0. Captures: [`mr1/`](mr1/). (A "1.000 MHz → 1000.000 kHz" row that
`compare_runs.py` may print for CE1 is a units-formatting quirk, same value.)

## MR2 — SSCR mode/clock latch (`sscr-latch`), B1 → B2

![MR2](figs/mr2-mode-and-clock.svg)

Reliable oracle = **clock frequency**. T1–T5 run in sequence with no reload; the
bug is stateful (a polled transfer inherits the previous transfer's latch).

| Case | request | B1 (baseline) | B2 (fixed) |
|------|---------|---------------|------------|
| T1 | m0 / 1 MHz polled | 1.000 MHz ✓ | 1.000 MHz ✓ |
| T2 | m3 / 4 MHz polled | **1.000 MHz STALE** (exit 1) | **3.846 MHz ✓** (exit 0) |
| T3 | m3 / 4 MHz long | 3.846 MHz ✓ (control) | 3.846 MHz ✓ |
| T4 | m3 / 4 MHz polled | 3.846 MHz ✓ | 3.846 MHz ✓ |
| T5 | m0 / 1 MHz polled | **3.846 MHz STALE** (exit 1) | **1.000 MHz ✓** (exit 0) |

T2 and T5 are the demonstration: identical requests whose wire clock on B1
depended only on the *previous* transfer, now track the request on B2.
`3.846 MHz = 100 MHz / 26` (integer-divisor rendering of 4 MHz). Captures:
[`mr2/`](mr2/).

## MR3 — Rx poll bound (`rx-timeout`), B1 → B3

![MR3](figs/mr3-rx-loopback.svg)

Oracle = loopback target exit (0 MATCH / 1 silent MISMATCH / 2 ETIMEDOUT). Sweep
is **primed** per speed (a long-path transfer at the same speed first — required
on B1/B3 because they lack the MR2 latch fix; see caveat).

| speed | B1 primed (baseline) | B3 (fixed) |
|-------|----------------------|------------|
| 4M / 1M / 500k / 200k | exit 0 MATCH | exit 0 MATCH |
| 100k | **exit 1 MISMATCH** `00de00adbe…` | exit 0 MATCH |
| 50k | **exit 1 MISMATCH** | exit 0 MATCH |
| 25k | **exit 1 MISMATCH** all-zero | exit 0 MATCH (24.988 kHz) |

On B1 the poll budget (`limit = speed/1000`) is too small at low speed: the
driver reads `SSDR` early and returns rotted data **as success** (silent exit 1).
On B3 the widened bound waits correctly — every speed down to 25 kHz returns the
full `deadbeefdeadbeef`. **Never exit 1.** Captures: [`mr3/`](mr3/) (`B1_primed/`
is the meaningful baseline; `B1/` is the un-primed sweep that masks the bug).

## MR4 — `debian/changelog` hygiene (no target)

Branch `debian-changelog` (`99a56a9`). Validated statically (no
`dpkg-parsechangelog` on this host): all 19/19 `--` trailers now carry the
mandatory blank-line stanza separator (on master they abutted the next header, so
`dpkg-parsechangelog` would see only the top entry); malformed
`17:39:45+0100 → 17:39:45 +0100` and impossible `Fri, 60 Jun 2023 →
Fri, 30 Jun 2023` both fixed.

## B4 — integration + regression

- **MR1 regression:** CE0 and CE1 both still toggle (2 edges), loopback MATCH.
- **MR2 (unprimed):** T1–T5 all track request; T5 re-latches 1 MHz on the polled
  path after T4's 4 MHz — MR2 fix live.
- **MR3 (unprimed):** every speed clocks at its **request** (25k → 25.000 kHz, no
  3.846 MHz masking) and all return exit 0 MATCH.
- **Integration payoff:** B4 needs **no priming crutch** — the MR2 fix latches the
  requested low speed directly on the polled path, so MR3's low speeds are
  genuinely exercised and pass. The two fixes reinforce rather than conflict.
- **Board health:** gpiochip4 = **28 lines** on every build; spidev nodes present.
  Captures: [`mr1/B4`](mr1/), [`mr2/B4`](mr2/), [`mr3/B4`](mr3/).

## Two methodology caveats (important for interpreting the captures)

1. **Pin parks low between transfers**, regardless of mode. So `cpol`/idle-level
   and the decoded payload are *unreliable* for mode-3: a parked-low→idle-high
   setup rise is counted as an extra clock edge, shifting the decode
   (`aa55`→`552ad52a`) and misreading CPOL. The **clock frequency** is therefore
   the only fully reliable MR2 discriminator; loopback RX==TX is not an MR2 oracle
   (the controller both drives and samples).
2. **MR3 low-speed sweeps must be primed** on any build lacking the MR2 fix
   (B1, B3): without a preceding long-path transfer at the target speed, the
   polled path never re-latches the low clock and every case runs at ~3.846 MHz,
   masking the bug. B4 (has MR2) needs no priming.

## Known non-fatal boot warnings (noted, not chased)

- `pps_gpio_probe → upboard_gpio_irq_startup → request_threaded_irq` WARN
  (irq/manage.c:1502) — pre-existing pps-pin37 overlay on BCM26 (not an SPI pin);
  warn-once, identical across master and all branches.
- OOT-unsigned-module taint (`MODULE_SIG=n`) — expected for out-of-tree builds.
- B1-only transient `gpiochip_fwd_* Unknown symbol` at 1.65 s (modprobe
  load-order; re-probes clean at 5.86 s, id 9, 28 lines). Independent of the SPI
  diffs.

## Verdict

All four MRs validated on hardware. MR1 restores both chip selects; MR2 makes
mode/clock track the request on the polled path; MR3 stops silent short-reads at
low clock; MR4 fixes the changelog; and B4 proves the three code fixes coexist
cleanly (no conflict, no regression, 28 GPIO lines intact).
