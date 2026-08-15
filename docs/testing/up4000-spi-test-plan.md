# UP 4000 SPI fixes — on-target test plan

Validation plan for the four branches split out of the original combined SPI
commit.  Every test is structured as **baseline first, fix second**, so each
branch produces a before/after pair rather than a bare "it works" claim.

| Branch | Claim to prove |
|---|---|
| `claude/spi-cs-apl03` | CS never toggles on UP 4000; after the fix it does |
| `claude/spi-sscr-latch` | Short transfers ignore requested mode/clock; after the fix they honour it |
| `claude/spi-rx-timeout` | Slow short transfers return garbage; after the fix they return correct data or `-ETIMEDOUT` |
| `claude/debian-changelog` | `debian/changelog` parses; package builds. No target needed |

Target board: **UP 4000** (`/sys/class/dmi/id/board_name` = `UP-APL03`,
board id 9 = `BOARD_UP_APL03`).

---

## 1. Test topology

**Run Claude Code on the Windows laptop. SSH into the UP 4000. Keep Logic 2
and the Saleae MCP server local to the laptop.**

The decisive reason is reboots. The install procedure below reboots the target
several times, and a Claude living on the target kills its own session every
time. A Claude on the laptop just waits for the SSH port to come back.

Two supporting reasons:

- Logic 2's automation server binds `127.0.0.1:10430`. Co-locating the MCP
  server means nothing has to be forwarded or exposed.
- Logic 2 writes `.sal` captures and CSV exports onto **its own** filesystem.
  With Claude on the laptop those files are directly readable. With Claude on
  the target, every single export would need an `scp` back before it could be
  analysed.

```
┌────────────────────────── Windows laptop ──────────────────────────┐
│  Claude Code                                                       │
│    ├── Saleae MCP server ── gRPC :10430 ── Logic 2 ── Logic 16 Pro │
│    │                                                          │    │
│    └── ssh ─────────────────────────────────────────────┐     │    │
└─────────────────────────────────────────────────────────┼─────┼────┘
                                                          │     │ probes
                                                    ┌─────▼─────▼─────┐
                                                    │  UP 4000        │
                                                    │  40-pin HAT     │
                                                    └─────────────────┘
```

### Alternatives considered

**Claude on the target, Logic 2 reverse-forwarded** (`ssh -R 10430:127.0.0.1:10430`).
Technically works for *issuing* capture commands, but captures and exports
still land on the laptop, so you need a second path back for every artifact —
and the session dies on every reboot. Not recommended.

**Logic 2 on Linux.** Logic 2 ships a Linux build. If you have a spare Linux
box, running Claude + Logic 2 + the MCP server there removes the Windows/WSL
friction entirely and is the cleanest option. Do **not** run Logic 2 on the
UP 4000 itself — it is the device under test and it reboots.

### Laptop prerequisites

- Logic 2 → Preferences → **enable the automation server** (default port 10430).
- Saleae MCP server configured in Claude Code, pointed at that port.
- OpenSSH client.
- If running Claude Code under **WSL2**: WSL cannot reach the Windows
  `127.0.0.1` by default. Either run Claude Code natively on Windows, or set
  `networkingMode=mirrored` in `.wslconfig` (Windows 11 22H2+), or add a
  `netsh interface portproxy` rule. Native Windows is the least trouble.

### Target prerequisites (required for autonomy)

```bash
# on the laptop
ssh-copy-id up@<target-ip>
# in ~/.ssh/config
Host up4000
    HostName <target-ip>
    User up
```

```bash
# on the target — passwordless sudo, or every step blocks on a password prompt
echo "up ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/up-test
sudo apt install -y build-essential linux-headers-$(uname -r) git python3-spidev
```

Reboot-and-wait pattern for the executing agent:

```bash
ssh up4000 sudo reboot
until ssh -o ConnectTimeout=5 -o BatchMode=yes up4000 true 2>/dev/null; do sleep 5; done
```

In Claude Code, prefer the `Monitor` tool with an until-condition over a
foreground `sleep` loop.

---

## 2. Probe wiring

UP 4000's 40-pin header is Raspberry Pi compatible. Confirmed against
`upboard_up_rpi_mapping[]` in `files/pinctrl-upboard.c`: index 7 → `SPI_CS1`,
8 → `SPI_CS0`, 9 → `SPI_MISO`, 10 → `SPI_MOSI`, 11 → `SPI_CLK`.

| Logic channel | Header pin | BCM | Signal |
|---|---|---|---|
| D0 | 23 | GPIO11 | SCLK |
| D1 | 19 | GPIO10 | MOSI |
| D2 | 21 | GPIO9 | MISO |
| D3 | 24 | GPIO8 | CE0 (`SPI_CS0`) |
| D4 | 26 | GPIO7 | CE1 (`SPI_CS1`) |
| GND | 25 | — | ground (use a short lead) |

**Loopback jumper: header pin 19 ↔ pin 21 (MOSI to MISO).** Leave it in for
every test. It is the oracle for MR 3 and harmless for the others.

Logic 2 capture settings: **digital only, 5 channels, 100 MS/s, 3.3 V logic
level**. 100 MS/s gives 25 samples per bit at 4 MHz, which is plenty.

> **Capture must be time-bounded, never triggered on a CS edge.** The whole
> point of the MR 1 baseline is that CS produces no edge — a CS trigger would
> wait forever. Use a fixed-duration capture: start capture, run the transfer
> over SSH, let the capture close.

---

## 3. Build and install

This is the step most likely to go wrong, so it is written to be ordered,
idempotent, and verifiable at every stage. **Do not use DKMS or `debuild` for
testing** — build out-of-tree and install the `.ko` files by hand.

### 3.1 One-time preparation

The stock DKMS package installs into `/lib/modules/$(uname -r)/updates/dkms/`.
Leaving it in place while you drop test modules into `updates/` creates two
candidates for the same module name and makes "which build is loaded?"
genuinely ambiguous. Remove it once, at the start:

```bash
sudo dkms status                                  # note the version, e.g. pinctrl-upboard/1.1.9
sudo dkms remove -m pinctrl-upboard -v 1.1.9 --all 2>/dev/null || true
sudo apt-get remove --purge -y pinctrl-upboard 2>/dev/null || true
ls /lib/modules/$(uname -r)/updates/dkms/ 2>/dev/null   # must be empty or gone
```

Now re-establish the pieces the package provided that are **not** kernel
modules — the ACPI overlays that declare the spidev devices, and the
blacklists that keep the in-tree `spi_pxa2xx_platform` off the controller:

```bash
git clone https://github.com/Trowel4153/pinctrl-upboard.git ~/pinctrl-upboard
cd ~/pinctrl-upboard/files
sudo make setup        # copies acpi/*.aml, writes blacklists, update-initramfs
sudo reboot
```

Verify after reboot:

```bash
grep -E 'spi_pxa2xx_(platform|core)' /etc/modprobe.d/blacklist.conf   # both present
ls /lib/firmware/acpi-upgrades/ | grep pci0.spi                        # overlays present
ls /sys/bus/spi/devices/                                               # spidev devices exist
```

### 3.2 Pre-flight: confirm the CS pads are actually mapped

MR 1 makes the probe do `readl(pctrl->pins[21].regs)` on this board for the
first time. `upboard_acpi_get_pins()` only fills `.regs` for indices below
`descs->ndescs`, so a short ACPI `external-gpios` list would leave that NULL
and the read would oops at probe.

The cheap check: `upboard_acpi_node_pin_mapping()` already iterates **all**
`npins` and returns an error if any pin is unmapped, so a clean probe with a
full 28-line gpiochip means all 28 pads are mapped.

```bash
sudo apt install -y gpiod
gpiodetect | grep -i "Raspberry Pi compatible UP GPIO"    # expect 28 lines
dmesg | grep -i upboard                                   # expect "compatible upboard id 9", no errors
```

28 lines and no probe error → indices 21/22 are safe. If it reports fewer,
**stop** and say so; MR 1 needs a NULL guard before it goes near this board.

Deeper check if you want certainty:

```bash
sudo apt install -y acpica-tools && mkdir -p /tmp/acpi && cd /tmp/acpi
sudo acpidump -b && iasl -d *.dat 2>/dev/null
grep -n "external-gpios" -B5 -A40 dsdt.dsl    # count GpioIo() entries: need >= 23
```

### 3.3 Building a test build

```bash
cd ~/pinctrl-upboard
git fetch origin
git checkout -f <branch>          # -f: see the protos.c note below
cd files && make -j$(nproc)
```

> **Gotcha:** the `check_protos` make target `sed`-edits `protos.c` **in
> place** as it probes kernel prototypes. Your tree is dirty after every
> build, and a plain `git checkout <other-branch>` will refuse to switch. Use
> `git checkout -f`, or `git checkout -- files/protos.c` first. This is
> pre-existing upstream behaviour, not something the fixes introduced.

### 3.4 Installing and proving which build is live

```bash
K=$(uname -r)
sudo mkdir -p /lib/modules/$K/updates
sudo cp ~/pinctrl-upboard/files/*.ko /lib/modules/$K/updates/
git -C ~/pinctrl-upboard rev-parse --short HEAD | sudo tee /etc/up-testbuild
sudo depmod -a
sudo reboot
```

Copy **all** the `.ko` files, not a subset: `pinctrl-upboard` needs
`upboard_pwm_register` from `pwm-upboard`, which in turn pulls `pwm-lpss`, and
`spi-pxa2xx-up` needs `upboard_set_cs` from `pinctrl-upboard`.

After reboot, verify the running modules are the ones you just built — this is
the check that removes all doubt:

```bash
echo "test build: $(cat /etc/up-testbuild)"
for m in pinctrl_upboard spi_pxa2xx_up; do
  f=/lib/modules/$(uname -r)/updates/$(echo $m | tr _ -).ko
  echo "$m  loaded=$(cat /sys/module/$m/srcversion)  ondisk=$(modinfo -F srcversion $f)"
done
```

The two `srcversion` hashes must match, and they must **change** between B0
and B1 (`pinctrl_upboard`) and between B1 and B2/B3 (`spi_pxa2xx_up`). If a
hash didn't change when you expected it to, you are testing the wrong build —
stop and fix that before capturing anything.

Reboot is the sanctioned path. For faster iteration you may try an unload
cycle, but verify `srcversion` afterwards and fall back to a reboot if any
`rmmod` fails:

```bash
sudo modprobe -r spidev; sudo rmmod spi_pxa2xx_up pinctrl_upboard pwm_upboard upboard_fpga
sudo modprobe upboard-fpga && sudo modprobe pinctrl-upboard && sudo modprobe spi-pxa2xx-up
```

### 3.5 Builds required

Each build differs from its comparison baseline by exactly one branch.

| Build | Contents | Used for |
|---|---|---|
| **B0** | `master` (d626a5e) | MR 1 baseline |
| **B1** | master + `claude/spi-cs-apl03` | MR 1 result; MR 2 and MR 3 baseline |
| **B2** | B1 + `claude/spi-sscr-latch` | MR 2 result |
| **B3** | B1 + `claude/spi-rx-timeout` | MR 3 result |
| **B4** | B1 + MR 2 + MR 3 | final integration |

B2 and B3 both sit on **B1, not on each other**, so each SPI capture isolates
one change. MR 2 and MR 3 need MR 1 in the baseline for a practical reason:
without CS toggling there is no frame boundary for the SPI analyser to key on.

```bash
git checkout -f -B testbuild master
git merge --no-edit origin/claude/spi-cs-apl03        # B1
git merge --no-edit origin/claude/spi-sscr-latch      # B2
```

### 3.6 Finding the spidev nodes

Do not hardcode `/dev/spidev1.0`; the bus number is assigned at probe.

```bash
ls -l /sys/bus/spi/devices/
for d in /sys/bus/spi/devices/spi*; do echo "$d -> $(cat $d/modalias 2>/dev/null)"; done
ls /dev/spidev*
```

The ACPI overlays declare `_HID "SPT0001"` on `\_SB.PCI0.SPI1` at CS0 and CS1,
so expect two nodes, `spidevB.0` and `spidevB.1`. Use `spidevB.0` (CE0,
header pin 24) throughout unless a test says otherwise.

---

## 4. MR 1 — chip select (`claude/spi-cs-apl03`)

**Claim:** on B0 the CS line never moves during a transfer; on B1 it asserts
low for the transfer and returns high.

Test case, run identically on both builds:

```bash
python3 ~/spi_case.py --bus <B> --dev 0 --mode 0 --speed 1000000 --len 8 \
                     --pattern aa55 --label mr1
```

Procedure per build: start a 500 ms timed capture → run the command over SSH →
let the capture close → export.

| Measurement | B0 (expected) | B1 (expected) |
|---|---|---|
| Edges on D3 (CE0) during capture | **0** | **2** (one falling, one rising) |
| CE0 low before first SCLK edge | no | yes |
| SPI analyser *with* enable line = CE0, decoded frames | **0** | **8 bytes** = `aa55aa55aa55aa55` |
| SPI analyser *without* enable line | 8 bytes | 8 bytes |
| SCLK / MOSI activity | present on both — the bus runs, only CS is dead | |

The last two rows are what make this airtight: the data is on the wire in both
builds, so the only thing that changed is CS. Run the analyser both ways.

**Also check CE1** (D4) with `--dev 1`, which exercises `cs_pins[1]` /
`SPI_CS1` / header pin 26. Same expectations.

Pass criteria: B0 shows zero CE0 edges and zero enable-gated frames; B1 shows
exactly one assert/deassert pair and the full 8 bytes decoded.

---

## 5. MR 2 — mode and clock rate (`claude/spi-sscr-latch`)

**Claim:** `up_spi_transfer()` writes SSCR0 with SSE already set, so SCR/DSS
and SPO/SPH are never latched and short transfers run with whatever the
*previous* transfer left behind.

Two things make this test work:

- Only transfers **< 32 bytes** take the polled path. 32 bytes and up go
  through `pxa2xx_spi_transfer_one()`, which already does the disable/enable
  dance correctly. That gives a built-in control.
- The bug is *stateful*. A single transfer proves nothing; you need a sequence
  where the requested config differs from the latched one.

**Reboot (or reload the modules) before T1, then run T1–T5 in order without
reloading.** Capture each case separately.

| Case | Request | Length | Path | B1 wire (buggy) | B2 wire (fixed) |
|---|---|---|---|---|---|
| T1 | mode 0, 1 MHz | 8 | polled | mode 0, 1 MHz | mode 0, 1 MHz |
| T2 | **mode 3, 4 MHz** | 8 | polled | **mode 0, 1 MHz** ← stale | mode 3, 4 MHz |
| T3 | mode 3, 4 MHz | 40 | long | mode 3, 4 MHz | mode 3, 4 MHz |
| T4 | mode 3, 4 MHz | 8 | polled | mode 3, 4 MHz — T3 re-latched it | mode 3, 4 MHz |
| T5 | **mode 0, 1 MHz** | 8 | polled | **mode 3, 4 MHz** ← stale again | mode 0, 1 MHz |

T2 and T5 are the demonstration: the *same request* produces *different wire
behaviour* on B1 depending only on what ran before it. T3 is the control that
proves the long path was always correct, so the defect is specific to the
polled path. T1 and T4 should look identical on both builds.

```bash
python3 ~/spi_case.py --mode 0 --speed 1000000 --len 8  --label t1
python3 ~/spi_case.py --mode 3 --speed 4000000 --len 8  --label t2
python3 ~/spi_case.py --mode 3 --speed 4000000 --len 40 --label t3
python3 ~/spi_case.py --mode 3 --speed 4000000 --len 8  --label t4
python3 ~/spi_case.py --mode 0 --speed 1000000 --len 8  --label t5
```

Measurements per capture, from the exported raw digital CSV:

- **CPOL** — SCLK level while CE0 is deasserted. Mode 0 idles **low**, mode 3
  idles **high**. This is the single clearest before/after signal.
- **Clock rate** — median SCLK period across the transfer. Expect ~1 MHz vs
  ~4 MHz. Report the measured value; the SSP divisor is integer so the
  achieved rate will be close but not exact.
- **CPHA** — which SCLK edge MOSI transitions on.
- **Decode check** — configure the SPI analyser for the *requested* mode. If
  the wire matches the request it decodes `aa55…`; if not, it produces garbage
  or nothing.

> The loopback jumper does **not** detect this bug. The controller both drives
> and samples, so RX matches TX even when the mode is wrong. For MR 2 the
> logic capture is the only oracle — do not accept a passing RX buffer as
> evidence.

Pass criteria: on B1, T2 measures CPOL=0 and ~1 MHz while requesting mode 3 /
4 MHz, and T5 measures CPOL=1 and ~4 MHz while requesting mode 0 / 1 MHz. On
B2, every case matches its request.

---

## 6. MR 3 — Rx poll bound (`claude/spi-rx-timeout`)

**Claim:** `limit = transfer->speed_hz/1000` is a spin count that shrinks
exactly as the per-word time grows. At 100 kHz it allows 100 MMIO reads
(roughly 30–100 µs depending on read latency) for a byte that needs 80 µs on
the wire. When it expires the driver reads SSDR anyway and returns stale data
as success.

**The loopback jumper is the oracle here.** RX must equal TX.

Sweep, on B1 then B3:

```bash
for s in 4000000 1000000 500000 200000 100000 50000 25000; do
  python3 ~/spi_case.py --mode 0 --speed $s --len 8 --pattern deadbeef --label "mr3-$s"
done
```

| Requested speed | Spin budget (`speed/1000`) | B1 expected | B3 expected |
|---|---|---|---|
| 4 MHz | 4000 | MATCH | MATCH |
| 1 MHz | 1000 | MATCH | MATCH |
| 500 kHz | 500 | MATCH or MISMATCH | MATCH |
| 200 kHz | 200 | MISMATCH likely | MATCH |
| 100 kHz | 100 | **MISMATCH** | MATCH |
| 50 kHz | 50 | **MISMATCH** | MATCH |
| 25 kHz | 25 | **MISMATCH** | MATCH |

The exact break-even depends on MMIO read latency on this SoC, so the sweep
*finds* the threshold rather than asserting one. What matters is that a
threshold exists on B1 and does not on B3.

Corroborate with the capture: at every speed, **MOSI and MISO carry identical
correct bytes on the wire**. That is the money shot — the data was there, and
the old driver returned garbage anyway. Export the analyser CSV for the
100 kHz case on both builds and put them side by side.

Note the SSP divisor floor. SCR is 12 bits on a ~100 MHz SSP clock, so
requests below roughly 25 kHz clamp. Measure the achieved rate from the
capture rather than trusting the request.

Also verify the new failure mode is *loud*. On B3 a genuine timeout must
surface as an error, not as bad data:

```bash
dmesg -w | grep -i "timeout waiting for Rx"     # in a second SSH session
```

`spi_case.py` exits **2** and prints `errno=110` (`ETIMEDOUT`) if the ioctl
fails. On B3 you should see either exit 0 with MATCH, or exit 2 — never exit 1
(MISMATCH). Exit 1 on B3 would mean the fix is incomplete.

---

## 7. MR 4 — changelog (`claude/debian-changelog`)

No target and no scope required. Anywhere with `devscripts`:

```bash
git checkout -f origin/claude/debian-changelog
dpkg-parsechangelog -l debian/changelog --all      # must print no warnings
sudo apt install -y dh-dkms devscripts debhelper
debuild -i -us -uc                                  # must reach a built .deb
```

Baseline contrast on `master`: the same `dpkg-parsechangelog` emits
`badly formatted trailer line` plus a pair of warnings per entry, and
`debuild` fails with `cannot parse maintainer email address ""`.

---

## 8. Final integration pass

Build **B4** (B1 + MR 2 + MR 3) and re-run the MR 1 case, the MR 2 T1–T5
sequence, and the MR 3 sweep. All must pass simultaneously. This catches any
interaction between the SSCR reordering and the new Rx loop, which live in the
same function.

Then confirm nothing else regressed:

```bash
gpiodetect && gpioinfo | head -40           # 28-line HAT gpiochip intact
ls /sys/class/pwm/                          # PWM unchanged vs B0 — MR 1 must NOT add a pwmchip
dmesg | grep -iE "upboard|pxa2xx|oops|BUG"  # clean
```

The `/sys/class/pwm/` check is deliberate. MR 1 gives UP 4000 its CS pads
**without** registering a PWM device, matching what the pre-`c026c3e` `default:`
case did. If a new `pwmchip` appears on B1 that did not exist on B0, the fix
went into the wrong switch case.

---

## 9. Reporting

Suggested artifact layout on the laptop:

```
captures/
  mr1/{B0,B1}/{ce0,ce1}.sal + .csv
  mr2/{B1,B2}/t{1..5}.sal + .csv
  mr3/{B1,B3}/{4M,1M,500k,200k,100k,50k,25k}.sal + .csv
  mr4/parsechangelog-{master,fix}.txt
logs/
  <build>-srcversion.txt      # proof of which build produced each capture
  <build>-dmesg.txt
```

Final report: one table per MR with the measured before/after values, each row
citing the capture file it came from. Record `/etc/up-testbuild` (git SHA) and
both `srcversion` values alongside every capture set — a capture without build
provenance proves nothing.

---

## 10. Known risks

1. **NULL pad registers.** Covered by the §3.2 pre-flight. If the gpiochip has
   fewer than 28 lines, MR 1 will oops at probe and needs a guard first.
2. **`protos.c` is modified by the build.** Always `git checkout -f`. §3.3.
3. **DKMS shadowing.** Remove the packaged version once, §3.1, or you cannot
   trust which module is loaded.
4. **ACPI overlays are separate from the modules.** They come from
   `make setup`, not from the `.ko` files. Removing the deb without re-running
   setup leaves you with no spidev nodes at all.
5. **CS-edge triggers hang the MR 1 baseline.** Timed captures only. §2.
6. **Nothing here is compile-tested yet.** The four branches were written
   without kernel headers available. Expect to fix build errors on first
   contact, and treat a clean `make` as the real first test.
