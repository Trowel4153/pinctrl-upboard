# Upstream issue drafts

Four issues to file against
[up-division/pinctrl-upboard](https://github.com/up-division/pinctrl-upboard),
one per problem, each matching one of the MR branches in this repo.

| Draft | Problem | Branch | Evidence today |
|---|---|---|---|
| [01](01-up4000-spi-chip-select.md) | SPI chip selects never driven on UP 4000 | `claude/spi-cs-apl03` | code + git history + **measured** |
| [02](02-spi-pxa2xx-short-transfer-mode-clock.md) | Transfers <32 B ignore mode and clock rate | `claude/spi-sscr-latch` | code + Intel PXA docs + **measured** |
| [03](03-spi-pxa2xx-polled-rx-spin-count.md) | Polled Rx spin count returns stale data | `claude/spi-rx-timeout` | code + **measured** |
| [04](04-debian-changelog-trailers.md) | `debian/changelog` does not parse | `claude/debian-changelog` | **verified** |

## Read this before filing

**All four have now been executed on hardware.** The test plan was run on a
UP 4000 (UP-APL03, kernel 7.0.0-22-generic) across five builds; 49 captures,
every CSV and `analysis.json` committed under
[`docs/testing/results/`](../testing/results/), with the write-up in
[`REPORT.md`](../testing/results/REPORT.md). Each draft now carries a
"Measured on a UP 4000" section, and the remaining `<!-- Fill in -->` comments
mark where the figures attach, not where the evidence is missing.

**One gap, and it is not cosmetic.** The capture runner recorded the wrong
module's `srcversion` — the stock in-tree `spi-pxa2xx-platform` rather than the
out-of-tree `spi-pxa2xx-up` under test — so all 49 captures carry an identical
fingerprint and none of them proves which SPI build produced it. The runner is
fixed; re-running the ladder is what closes this. Until it is re-run, do not
claim in an issue that the builds are individually attested. The behavioural
evidence is unaffected — what changes between directories is exactly what each
fix predicts — but "we rebooted into five builds" is currently an assertion.

Filing order, strongest first:

- **Draft 01** is the easiest to confirm. It names a specific commit that
  removed a specific `default:` case, checkable from history in a minute, and
  the measurement adds that both selects sit stuck *asserted* rather than
  merely inert — worse than the original claim, and clearer.
- **Draft 02** is now the best-evidenced. Its CPOL/CPHA claim is backed by
  Intel's own wording in the PXA27x Developer's Manual, quoted with page
  numbers; its clock-rate claim, which has no such documentary backing, is
  carried by the measurement instead. The two halves are supported by different
  kinds of evidence and the draft says which is which.
- **Draft 03** gained the strongest new finding of the run: the baseline
  releases chip select while the SSP is still shifting — 127 of 128 clock edges
  arrive after the frame closes at 25 kHz. That is a framing violation visible
  to any slave, not just a bad buffer in one process, and it needs no loopback
  jumper to reproduce.
- **Draft 04** is unchanged and still the thirty-second merge.

## Figures

Each SPI draft has a marked spot for a before/after waveform, rendered by
`docs/testing/analysis/plot_capture.py` (§9 of the test plan). The panel
captions are produced by the same decoder that `analyze_capture.py` asserts on,
so the numbers under the figure are measurements rather than annotations
someone typed — which is exactly the property that makes a figure worth putting
in front of a maintainer.

**The real ones are in
[`docs/testing/results/figs/`](../testing/results/figs/)** — `mr1-chip-select.svg`,
`mr2-mode-and-clock.svg`, `mr3-rx-loopback.svg`. Attach those.

The similarly named files under `../testing/analysis/examples/` are rendered
from **synthetic fixtures and must not be attached to an issue.** They exist so
you can see what the tool produces without a bench. Check the footer before you
attach anything: every real figure carries build identifiers there, so a figure
that escapes into a thread still says which builds produced it, and a figure
without one is a fixture.

For the issue body itself, commit the SVG somewhere GitHub can serve it and
link it — GitHub renders SVG from a repository, the same mechanism badges use.
For a drag-and-drop attachment, pass `--png` as well; PNG is the safe format
for upload.

One thing not to attach: `loopback match` as corroboration. Header pins 19 and
21 are jumpered on this bench, so MOSI and MISO are a single probed node and a
single CSV column — the analyser comparing them is comparing a column with
itself. The decoder now reports `loopback_shared_channel` and suppresses the
comparison. Where a round trip is the evidence (draft 03), it is the target's
own `rx=` line, read back through the driver.

## Filing them

Each draft's `<h1>` is the issue title; everything after it is the body. Drop
the `**Labels:**` line into the label field rather than the body.

File them as four separate issues, not one. They have different root causes, and
02 and 03 sit in the same function but are independent — either could be fixed
without the other. Cross-reference them ("related to #N") once the numbers
exist.

## Then the PRs

Yes — issue first, PR second, one PR per issue, is the right shape here, and the
branches are already cut that way.

**Check the fork relationship first.** GitHub only offers a cross-repository
pull request if `Trowel4153/pinctrl-upboard` is a *fork* of
`up-division/pinctrl-upboard`. If it was created by pushing a clone into a fresh
empty repo, GitHub will not connect them and the "compare across forks" link
will not appear.

```bash
curl -s https://api.github.com/repos/Trowel4153/pinctrl-upboard | \
    grep -E '"fork"|"parent"'
```

If `"fork": false`, fork upstream properly and push these branches to the fork,
or open the PRs from a fresh fork. Nothing needs rewriting — the commits are
portable.

**Rebase before opening.** The branches are based on `d626a5e`. Check whether
upstream's default branch has moved and rebase onto its current head, so the PR
diff is only the fix.

**They are independent.** 02 and 03 touch the same function
(`up_spi_transfer()`), but their hunks are far enough apart that the branches
merge cleanly in either order — verified, not assumed:

```bash
git checkout -b t claude/spi-sscr-latch
git merge --no-commit --no-ff claude/spi-rx-timeout   # clean
```

So all four can be open at once and merged in whatever order the maintainers
prefer. Worth saying so in the PR descriptions; a reviewer looking at two PRs
against the same function will otherwise assume they are a stack.

**Keep each PR to one fix.** The four branches were split out of a single
combined commit precisely so a maintainer can take the changelog fix in thirty
seconds without adjudicating the SSP latch argument, and can reject one without
rejecting the others.

**Link them.** `Fixes #N` in each PR description, so the issue closes when the
PR merges and the reasoning stays reachable from the code.

## What the maintainers are being asked to decide

Three genuine open questions are called out in the drafts. They are not
rhetorical — we have a UP 4000 but not the other boards, nor the board
knowledge to settle them, and a maintainer answering any of them changes what
the PR should contain:

1. Should `BOARD_UPN_EHL01`, `BOARD_UP_ISH` and `BOARD_AIOT_IP6801` get the same
   chip select fix? They regressed identically in c026c3e. (Draft 01)
2. Should UP 4000 register a PWM chip? The history says the pre-c026c3e
   `default:` gave chip selects without PWM, so the minimal fix does the same.
   (Draft 01)
3. Is failing the transfer with `-ETIMEDOUT` the wanted behaviour, given today's
   code silently returns wrong data instead? (Draft 03) — note that the run
   never exercised this path: the widened bound was sufficient at every speed
   down to 25 kHz, so the timeout never fired and the error behaviour is
   reasoned about rather than measured.
