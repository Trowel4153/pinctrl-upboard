# Upstream issue drafts

Four issues to file against
[up-division/pinctrl-upboard](https://github.com/up-division/pinctrl-upboard),
one per problem, each matching one of the MR branches in this repo.

| Draft | Problem | Branch | Evidence today |
|---|---|---|---|
| [01](01-up4000-spi-chip-select.md) | SPI chip selects never driven on UP 4000 | `claude/spi-cs-apl03` | code + git history |
| [02](02-spi-pxa2xx-short-transfer-mode-clock.md) | Transfers <32 B ignore mode and clock rate | `claude/spi-sscr-latch` | code + Intel PXA docs |
| [03](03-spi-pxa2xx-polled-rx-spin-count.md) | Polled Rx spin count returns stale data | `claude/spi-rx-timeout` | code |
| [04](04-debian-changelog-trailers.md) | `debian/changelog` does not parse | `claude/debian-changelog` | **verified** |

## Read this before filing

**Only draft 04 has been executed.** The three SPI drafts are derived from
reading the driver and its history; the branches that fix them have never been
compiled, let alone booted. Each draft carries an HTML comment marking where the
measured evidence goes:

```
<!-- Fill in before filing: ... -->
```

`docs/testing/up4000-spi-test-plan.md` exists to produce exactly those
artifacts. Running it first is worth it: a maintainer reading "measured mode 0
at 1 MHz where mode 3 at 4 MHz was requested, capture attached" acts on it,
where "we think the SSP does not latch SSCR0 while enabled" invites a debate
about whether the reporter read the datasheet correctly.

Draft 01 is the exception worth considering filing early — it names a specific
commit that removed a specific `default:` case, which a maintainer can confirm
from the history alone in about a minute.

Draft 02 is the next strongest. Its CPOL/CPHA claim is now backed by Intel's
own wording in the PXA27x Developer's Manual, quoted with page numbers in the
draft, so that half does not rest on our reading of the driver. Its clock-rate
claim does not have the same backing and the draft says so explicitly — see the
caveat under "What the documentation says". Do not quietly promote the two to
equal footing when filing.

## Figures

Each SPI draft has a marked spot for a before/after waveform, rendered by
`docs/testing/analysis/plot_capture.py` (§9 of the test plan). The panel
captions are produced by the same decoder that `analyze_capture.py` asserts on,
so the numbers under the figure are measurements rather than annotations
someone typed — which is exactly the property that makes a figure worth putting
in front of a maintainer.

Two example figures, rendered from synthetic fixtures, show the output:
[`mr1-chip-select.svg`](../testing/analysis/examples/mr1-chip-select.svg) and
[`mr2-mode-and-clock.svg`](../testing/analysis/examples/mr2-mode-and-clock.svg).

**Those two are fixtures, not measurements, and must not be attached to an
issue.** They exist so you can see what the real ones will look like. Every
figure the tool renders takes a `--footer`; put the build identifiers from each
capture's `run.json` there, so a figure that escapes into a thread still says
which builds produced it.

For the issue body itself, commit the SVG somewhere GitHub can serve it and
link it — GitHub renders SVG from a repository, the same mechanism badges use.
For a drag-and-drop attachment, pass `--png` as well; PNG is the safe format
for upload.

If you would rather file before testing, say so in the issue rather than letting
the omission speak: *"Analysis from reading the driver; we have a UP 4000 on the
bench and will attach captures."* That is a fine issue. An issue that implies
measurement it does not have is not.

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
rhetorical — we do not have the hardware or the board knowledge to settle them,
and a maintainer answering any of them changes what the PR should contain:

1. Should `BOARD_UPN_EHL01`, `BOARD_UP_ISH` and `BOARD_AIOT_IP6801` get the same
   chip select fix? They regressed identically in c026c3e. (Draft 01)
2. Should UP 4000 register a PWM chip? The history says the pre-c026c3e
   `default:` gave chip selects without PWM, so the minimal fix does the same.
   (Draft 01)
3. Is failing the transfer with `-ETIMEDOUT` the wanted behaviour, given today's
   code silently returns wrong data instead? (Draft 03)
