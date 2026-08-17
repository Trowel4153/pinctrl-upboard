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

**Where to start.** If you are setting the bench up, start at **§1** — it is
the human-only part: wiring, GUI settings, credentials, and the recovery plan
for when a build misbehaves. If you are the agent executing the run, start at
**§2** and treat §1 as already done; §1.6 is the smoke test that says it is.

---

## 1. Human setup — do this before handing off to Claude

Everything in this section needs hands, a GUI, a password, or a judgement call
an agent should not be making on its own. The rest of the plan is written to
be executed by either a human or an agent; this section is not.

Work through it in order and tick each box. The handoff smoke test in §1.6 is
the gate — if it passes, the agent has everything it needs.

### 1.1 Bench, physical

- [ ] UP 4000 powered, on the network, reachable by IP. Note the IP.
- [ ] **Physical access retained** — monitor and keyboard attached, or a serial
      console. See §1.5; this is not optional.
- [ ] Logic 16 Pro connected to the laptop by USB.
- [ ] Probe leads on the header per the §3 table: D0→pin 23, D1→pin 19,
      D2→pin 21, D3→pin 24, D4→pin 26, **GND→pin 25**.
- [ ] **Loopback jumper: header pin 19 ↔ pin 21.** MR 3 cannot be tested
      without it, and it does no harm to the others.
- [ ] Count pins twice, from the pin-1 marker. A silently miswired D3 will look
      exactly like "CS never toggles", which is the very thing MR 1 is
      supposed to prove. §1.6 includes a check that catches this.

Nothing here is hot-pluggable in any meaningful sense, but power the target
down before moving probe leads anyway.

### 1.2 Logic 2, in the GUI

The automation API cannot turn itself on, and it cannot set the logic level.

- [ ] Install Logic 2 on the laptop, launch it, confirm the device appears.
- [ ] Preferences → **enable the automation server**. Leave the port at 10430
      unless you have a reason; if you change it, tell the agent.
- [ ] Set the digital **logic level to 3.3 V**. The UP 4000 header is 3.3 V and
      a wrong threshold produces plausible-looking garbage rather than an
      obvious failure.
- [ ] Enable channels D0–D4, set the sample rate to **100 MS/s**, digital only.
- [ ] Name the channels SCLK / MOSI / MISO / CE0 / CE1. Cosmetic, but it makes
      every exported CSV self-describing.
- [ ] Decide where captures are written and make sure the directory exists.
      Give that path to the agent in §1.7.

### 1.3 Claude Code and the automation API, on the laptop

Captures are driven by the **Logic 2 automation API** — a documented Python
package over gRPC — not by an MCP server. §9 explains the choice. An MCP server
is a useful extra for poking at the bus by hand, but nothing in this plan
depends on one.

- [ ] Claude Code installed on the **laptop**, not on the target. §2 explains
      why; the short version is that the target reboots repeatedly.
- [ ] `pip install logic2-automation`. The package documents support for
      **Python 3.8–3.10**, so if your default interpreter is newer, make a
      dedicated venv and tell the agent its path. The tools in
      `docs/testing/analysis/` run on 3.8 or later either way.
- [ ] Logic 2 **2.4.0 or later** — earlier releases have no automation server.
- [ ] If you are using WSL2, resolve the `127.0.0.1` reachability problem now —
      mirrored networking, a portproxy, or just run Claude Code natively on
      Windows. See §2.
- [ ] Clone this repository on the laptop so the agent has the plan,
      `spi_case.py` and the analysis tools in `docs/testing/analysis/` locally.
- [ ] Run the analysis self-tests once — no hardware needed, and it means the
      first real capture is not also the first execution of that code:
      `cd docs/testing/analysis && python3 test_analysis.py` (§9).
- [ ] Rehearse a whole case with no hardware at all, which also proves the
      Python environment is sane:
      `python3 capture_runner.py --out /tmp/rehearsal --label demo
      --simulate mode=0,hz=1e6,bytes=aa55aa55,cs-dead --expect-cs-edges 0`

### 1.4 Credentials — the parts that need a password

An agent cannot type a password into an interactive prompt. Each of these is a
one-time human action that makes the whole run non-interactive afterwards.

- [ ] SSH key onto the target: `ssh-copy-id up@<target-ip>`.
- [ ] `~/.ssh/config` entry so the agent can just say `up4000` (§2).
- [ ] **Connect once by hand** to accept the host key. A first-connection
      prompt will otherwise stall the agent's first command.
- [ ] Passwordless sudo on the target:
      `echo "up ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/up-test`
- [ ] Install the packages that need sudo:
      `sudo apt install -y build-essential linux-headers-$(uname -r) git python3-spidev gpiod`

Passwordless sudo on a test board on your bench is fine. Do not leave it on a
box that does anything else.

### 1.5 Recovery plan — the one thing an agent cannot do for you

MR 1 makes the pinctrl probe read a pad register on this board for the first
time. The §4.2 pre-flight is there to establish that the read is safe, but the
honest position is that these branches have never been compiled, let alone
booted. Assume at least one build will misbehave.

- [ ] Know how to get a GRUB menu on this board (hold or tap `Shift`, or `Esc`,
      during boot).
- [ ] Know the escape hatch: at the GRUB menu press `e`, append
      **`modprobe.blacklist=pinctrl_upboard,spi_pxa2xx_up,upboard_fpga`** to the
      `linux` line, `Ctrl-X` to boot. That gets you a shell with the test
      modules inert, from which you can delete
      `/lib/modules/$(uname -r)/updates/*.ko` and `depmod -a`.
- [ ] Keep the original `pinctrl-upboard_*.deb` somewhere you can reach it, so
      you can put the board back to stock in one command.
- [ ] **Be at the console for the first boot of B1** (the first build carrying
      MR 1). Later reboots are safe to leave to the agent; that one is not.
- [ ] If the board hangs, the agent will report an SSH timeout and stop. It
      cannot power-cycle. That is your job.

### 1.6 Handoff smoke test

Run these before starting the real work. All of them must pass without
prompting you for anything.

**Target reachable, sudo silent, board is what we think it is:**

```bash
ssh -o BatchMode=yes up4000 \
  'sudo true && echo SUDO_OK; cat /sys/class/dmi/id/board_name; uname -r'
```

Expect `SUDO_OK`, then `UP-APL03`, then the kernel version. A password prompt
or a host-key question here means §1.4 is incomplete.

**Reboot-and-return works unattended:**

```bash
ssh up4000 sudo reboot
until ssh -o ConnectTimeout=5 -o BatchMode=yes up4000 true 2>/dev/null; do sleep 5; done
echo "target came back"
```

**Claude can drive the scope.** Two commands, both from
`docs/testing/analysis/`. The first proves the automation server is up and the
Logic is attached; the second takes a real capture, exports it, labels the
columns and measures it:

```bash
python3 capture_runner.py --out /tmp/x --list-devices
python3 capture_runner.py --out ~/captures/smoke --label smoke \
    --target up4000 --seconds 2 \
    --spi 'python3 ~/spi_case.py --mode 0 --speed 1000000 --len 8'
```

The second must print a decoded `mosi` value. If it does, every later case is
the same command with different expectations. A `no CSV export found` or a
connect timeout means the automation server checkbox in §1.2 is off, or the
port differs.

**The probes are on the pins you think they are.** This is the check that
catches a miswire before it masquerades as a test result. With the stock or B0
build loaded, have the agent start a capture while you drive each CS line as a
plain GPIO:

```bash
# on the target; BCM8 = CE0 = header pin 24, BCM7 = CE1 = header pin 26
gpioset $(gpiofind GPIO8 | cut -d' ' -f1) $(gpiofind GPIO8 | cut -d' ' -f2)=0
sleep 1
gpioset $(gpiofind GPIO8 | cut -d' ' -f1) $(gpiofind GPIO8 | cut -d' ' -f2)=1
```

D3 must move, and nothing else must. Repeat for GPIO7 and D4. If the wrong
channel moves, fix the wiring now — not after you have captured a day of data.
Use `gpioinfo` to confirm the line names on your kernel's libgpiod version.

### 1.7 What to tell the agent

Hand over these specifics; everything else it can discover.

| Item | Example |
|---|---|
| SSH alias for the target | `up4000` |
| Capture output directory on the laptop | `C:\up-test\captures` |
| Saleae automation port, if not default | `10430` |
| Python interpreter with `logic2-automation` | `~/venvs/saleae/bin/python` |
| Channel map, if not D0–D4 in plan order | `MOSI=0,MISO=1,SCLK=2,CE0=3,CE1=4` |
| Repo checkout on the laptop | `~/pinctrl-upboard` |
| Whether the §4.1 one-time prep is already done | yes / no |

A kickoff prompt along these lines works:

> Execute `docs/testing/up4000-spi-test-plan.md`. The target is the SSH host
> `up4000`; passwordless sudo is set up. Logic 2's automation server is on
> 10430 and captures go to `<path>`; drive it with
> `docs/testing/analysis/capture_runner.py`. Section 4.1 one-time prep is
> **not** done yet — start there. Do the §4.2 pre-flight before building
> anything with MR 1 in it, and **stop and ask me** if the gpiochip does not
> report 28 lines. Work through MR 1, then MR 2, then MR 3, capturing baseline
> and fix for each, and give me the §11 report at the end. I will be at the
> console for the first B1 boot — tell me before you reboot into it.

### 1.8 When the agent will need you

Expect to be interrupted for these, and only these:

- The first B1 boot (§1.5), by prior arrangement.
- A failed §4.2 pre-flight — fewer than 28 gpiochip lines means MR 1 needs a
  code change before it is safe to boot, which is a decision, not a step.
- A build failure that is not an obvious typo. These branches are uncompiled;
  a real portability problem against your kernel version is worth looking at
  together.
- A hung target (§1.5).
- Re-seating a probe if a channel reads flat when the others are active.

Everything else — building, installing, rebooting, capturing, exporting,
decoding, tabulating — should run without you.

---

## 2. Test topology

**Run Claude Code on the Windows laptop. SSH into the UP 4000. Keep Logic 2 and
the automation server local to the laptop.**

The decisive reason is reboots. The install procedure below reboots the target
several times, and a Claude living on the target kills its own session every
time. A Claude on the laptop just waits for the SSH port to come back.

Two supporting reasons:

- Logic 2's automation server binds `127.0.0.1:10430`. Running the capture
  script on the same machine means nothing has to be forwarded or exposed.
- Logic 2 writes `.sal` captures and CSV exports onto **its own** filesystem.
  With Claude on the laptop those files are directly readable. With Claude on
  the target, every single export would need an `scp` back before it could be
  analysed.

```
┌───────────────────────────── Windows laptop ─────────────────────────────┐
│  Claude Code                                                             │
│    └── capture_runner.py                                                 │
│          ├── logic2-automation ── gRPC :10430 ── Logic 2 ── Logic 16 Pro │
│          │                                                          │    │
│          └── ssh ───────────────────────────────────────────┐       │    │
└─────────────────────────────────────────────────────────────┼───────┼────┘
                                                              │       │ probes
                                                        ┌─────▼───────▼───┐
                                                        │  UP 4000        │
                                                        │  40-pin HAT     │
                                                        └─────────────────┘
```

One process owns both halves of a case, which is what makes the ordering
reliable: the capture is running before the target touches a pin, and it is
stopped after the transfer returns.

### Alternatives considered

**Claude on the target, Logic 2 reverse-forwarded** (`ssh -R 10430:127.0.0.1:10430`).
Technically works for *issuing* capture commands, but captures and exports
still land on the laptop, so you need a second path back for every artifact —
and the session dies on every reboot. Not recommended.

**A Saleae MCP server instead of the automation API.** See §9; the short
version is that the automation API is the thing an MCP server wraps, and this
plan needs the same capture taken identically about twenty times. Keep an MCP
server around for interactive poking if you have one — it just is not what the
matrix runs on.

**Logic 2 on Linux.** Logic 2 ships a Linux build. If you have a spare Linux
box, running Claude + Logic 2 + the automation server there removes the Windows/WSL
friction entirely and is the cleanest option. Do **not** run Logic 2 on the
UP 4000 itself — it is the device under test and it reboots.

### Prerequisites

Laptop, target and credential setup all live in §1 so there is one copy of
each command. The WSL2 `127.0.0.1` problem is §1.3; passwordless SSH and sudo
are §1.4.

### Reboot handling

The agent reboots the target repeatedly and must wait for it to return:

```bash
ssh up4000 sudo reboot
until ssh -o ConnectTimeout=5 -o BatchMode=yes up4000 true 2>/dev/null; do sleep 5; done
```

In Claude Code, prefer the `Monitor` tool with an until-condition over a
foreground `sleep` loop.

---

## 3. Probe wiring

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

## 4. Build and install

This is the step most likely to go wrong, so it is written to be ordered,
idempotent, and verifiable at every stage. **Do not use DKMS or `debuild` for
testing** — build out-of-tree and install the `.ko` files by hand.

### 4.1 One-time preparation

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

### 4.2 Pre-flight: confirm the CS pads are actually mapped

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

### 4.3 Building a test build

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

### 4.4 Installing and proving which build is live

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

### 4.5 Builds required

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

### 4.6 Finding the spidev nodes

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

## 5. MR 1 — chip select (`claude/spi-cs-apl03`)

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

## 6. MR 2 — mode and clock rate (`claude/spi-sscr-latch`)

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
| T4 | mode 3, 4 MHz | 8 | polled | **mode 0**, 4 MHz — rate re-latched by T3, polarity not | mode 3, 4 MHz |
| T5 | **mode 0, 1 MHz** | 8 | polled | mode 0, **4 MHz** ← stale rate | mode 0, 1 MHz |

The B1 column is what the hardware actually did, not a prediction — see
`results/REPORT.md`. An earlier draft of this table predicted that mode would
be *inherited* on B1 the way the clock rate is, so that T4 would show mode 3
and T5 would show CPOL=1. It does not work that way. On B1 the polled path
never puts the requested polarity on the wire **at all**: T3 is a long-path
transfer that genuinely leaves CPOL=1, and T4 immediately after it — same
requested mode, polled — comes back CPOL=0 while keeping T3's clock divisor.

That asymmetry is the sharpest single observation in the whole run, and it is
worth aiming the test at deliberately: the rate half of a request survives on a
running port and the polarity half does not, which is what Intel's text
predicts for `SSCR1` written with `SSE` set.

T2 and T5 are the stateful demonstration for the clock: the *same request*
produces *different wire behaviour* on B1 depending only on what ran before it.
T3 is the control that proves the long path was always correct, so the defect is
specific to the polled path.

```bash
python3 ~/spi_case.py --mode 0 --speed 1000000 --len 8  --label t1
python3 ~/spi_case.py --mode 3 --speed 4000000 --len 8  --label t2
python3 ~/spi_case.py --mode 3 --speed 4000000 --len 40 --label t3
python3 ~/spi_case.py --mode 3 --speed 4000000 --len 8  --label t4
python3 ~/spi_case.py --mode 0 --speed 1000000 --len 8  --label t5
```

Measurements per capture, from the exported raw digital CSV:

- **CPOL** — SCLK level **after the last clock edge of the frame**. Mode 0
  idles low, mode 3 idles high. This is the single clearest before/after signal,
  but read it from the *trailing* idle, never the leading one. The UP 4000
  parks SCLK low between messages, so a mode-3 transfer opens with a setup rise
  inside the CS window; take that rise for a clock edge and you will read CPOL
  inverted and shift every decoded byte by a bit. `analyze_capture.py` trims
  setup and teardown transitions and reports `setup_edges_trimmed` when it
  does. A 64-bit transfer showing 129 SCLK edges instead of 128 is the tell.
- **Clock rate** — median SCLK period across the transfer. Expect ~1 MHz vs
  ~4 MHz. Report the measured value; the SSP divisor is integer so the
  achieved rate will be close but not exact.
- **CPHA** — which SCLK edge MOSI transitions on. Needs a payload that
  transitions every bit, which is why the cases use `aa55`.

```bash
analyze_capture.py t2.csv --cs CE0 --expect-mode 3 --expect-hz 4e6
```

> **The decoded bytes cannot tell you the mode.** An earlier draft of this plan
> claimed you could set the analyser to the requested mode and read garbage
> when the wire disagreed. That is wrong, and the analysis self-tests
> (§9) disprove it: when the data line is held for a full clock period, every
> mode samples inside the valid window, so all four decode `aa55…` identically.
> Assert on **CPOL, CPHA and clock rate**. Never on the payload.

> The loopback jumper does **not** detect this bug either. The controller both
> drives and samples, so RX matches TX even when the mode is wrong. For MR 2
> the measured clock idle level and rate are the only oracle — do not accept a
> passing RX buffer, or a clean decode, as evidence.

Pass criteria: on B1, T2 measures mode 0 at ~1 MHz while requesting mode 3 at
4 MHz; T4 measures **mode 0** at ~4 MHz while requesting mode 3 at 4 MHz; and
T5 measures ~4 MHz while requesting 1 MHz. On B2, every case matches its
request in **both** mode and rate.

Record mode and rate for every case and judge them separately — they fail
independently on B1, which is the point. A case that matches its request in
both on B1 is the only outcome that would contradict the report.

---

## 7. MR 3 — Rx poll bound (`claude/spi-rx-timeout`)

**Claim:** `limit = transfer->speed_hz/1000` is a spin count that shrinks
exactly as the per-word time grows. At 100 kHz it allows 100 MMIO reads
(roughly 30–100 µs depending on read latency) for a byte that needs 80 µs on
the wire. When it expires the driver reads SSDR anyway and returns stale data
as success.

**Two oracles, and the second one is better.**

1. The loopback round trip: the **target's** `rx=` must equal `tx=`. Note that
   this has to come from `spi_case.py` on the target, not from the analyser —
   with pins 19 and 21 jumpered, MOSI and MISO are one probed node, so an
   analyser comparing them is comparing a column with itself.
2. **Chip select must still be asserted when the last clock edge lands.** On the
   buggy build it is not: the controller releases CS while the SSP is still
   shifting, so a slave sees the frame torn mid-word. `analyze_capture.py`
   reports this as `clock_edges_after_frame` / `frame_truncated`. Measured on
   B1: 49 of 128 edges arrive after CS releases at 100 kHz, 113 at 50 kHz, and
   127 at 25 kHz — at 25 kHz the frame closes before the second clock edge.

Oracle 2 is the one to lead with. It needs no jumper, it is visible to any
slave on the bus rather than only to a loopback, and on the run in `results/`
it agreed with the target's own verdict on all 28 MR3 captures: zero on each of
the 25 that succeeded, non-zero on exactly the 3 that came back wrong.

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

Corroborate with the capture — but not by comparing MOSI against MISO, which
on this bench is one column compared with itself. The capture's contribution is
`frame_truncated`: the clock keeps running after CS releases on exactly the
speeds that fail. Export the CSV for the 100 kHz case on both builds and put
them side by side; the baseline panel shows the burst continuing past the
chip-select edge.

**Prime each speed** on any build without the MR2 fix (B1, B3). A polled
transfer there inherits the previous transfer's clock divisor, so an unprimed
sweep runs every case at ~3.846 MHz and the low speeds are never exercised —
the bug hides completely. Precede each case with a ≥32-byte transfer at the
same speed, which takes the long path and latches the divisor. B4 has MR2 and
needs no priming; that is one of the reasons to test the combination.

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

On the run in `results/`, exit 2 never occurred: the widened bound was
sufficient at every speed down to 25 kHz, so the timeout path was not exercised
on hardware. Say that plainly when filing draft 03, because it bears directly
on the open question there about whether failing with `-ETIMEDOUT` is the
wanted behaviour — nothing in this run demonstrates the timeout firing.

---

## 8. MR 4 — changelog (`claude/debian-changelog`)

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

## 9. Analysis tooling

`docs/testing/analysis/` holds the tools that turn a capture into the numbers
this plan asks for. They run on the laptop on plain Python 3; only
`capture_runner.py` needs anything installed, and only when it is talking to a
real Logic.

| File | Purpose |
|---|---|
| `capture_runner.py` | one case end to end: arm, drive the target, export, measure |
| `salcap.py` | parsing and measurement library |
| `analyze_capture.py` | one capture → measurements, expectation checks, JSON |
| `plot_capture.py` | one or two captures → an annotated waveform figure |
| `compare_runs.py` | two JSON results → the before/after table for an MR |
| `make_fixture.py` | synthesise a capture with a known waveform |
| `test_analysis.py` | self-tests, no hardware needed |

### Drive Logic 2 through the automation API, not an MCP server

Saleae publish a [Python automation
API](https://saleae.github.io/logic2-automation/) over gRPC: `Manager.connect`,
`start_capture`, `export_raw_data_csv`, `save_capture`. That is what
`capture_runner.py` uses, and it is the right layer for this job for four
reasons.

- **It is what an MCP server wraps.** A Saleae MCP server is a translation of
  this same gRPC interface into tool calls. Using the API directly removes a
  layer whose behaviour would have to be discovered at the bench rather than
  read from a reference.
- **The same capture is taken about twenty times.** Four builds, five MR-2
  cases, a clock sweep. Sample rate, threshold, channel set and export options
  have to be byte-identical across all of them or the comparison is not a
  comparison. A script guarantees that; a tool call re-decided each time by a
  model does not.
- **The ordering is tight and matters.** The capture has to be running before
  the target touches a pin and stopped after the transfer returns — two
  machines, one window. `capture_runner.py` owns both halves in one process.
  Conversational tool calls interleave at the model's pace.
- **Everything can be asserted and rehearsed.** A case is a command line with
  an exit status, so the agent can gate on it, and `--simulate` runs the entire
  path with a synthetic capture before the bench is even wired.

None of this argues against having an MCP server. It is genuinely better for
the thing scripts are bad at: looking at the bus when something surprising
happens, mid-run, without writing code first. Use it for triage; use the
automation API for the matrix.

### Running a case

```bash
python3 capture_runner.py --out runs/mr1-b0 --label mr1-b0 \
    --target up4000 \
    --spi 'python3 ~/spi_case.py --mode 0 --speed 1000000 --len 8' \
    --expect-cs-edges 0 --expect-mosi aa55aa55aa55aa55
```

That connects to Logic 2, starts a manual capture, runs the transfer over SSH,
stops the capture, exports the raw CSV and a `.sal`, relabels the CSV columns
`SCLK`/`MOSI`/`MISO`/`CE0`/`CE1`, and hands it to `analyze_capture.py`. Exit
status is the analysis result: 0 all expectations held, 1 a measurement
contradicted one, 2 the capture was unreadable, 3 the capture never happened.

**Exit 1 is a result, not a malfunction.** On a baseline build, the
expectations that describe the fixed behaviour are supposed to fail. That is
the evidence.

Each run directory ends up holding `digital.csv`, `capture.sal`,
`analysis.json`, and a `run.json` recording the label, the channel map, the
target command with its own stdout and exit status, and the build provenance
read off the target at capture time — `/etc/up-testbuild`, `uname -r`, and the
module `srcversion`. A capture therefore cannot be separated from the build
that produced it, which is the failure mode this plan is most exposed to.

Useful flags: `--seconds N` for a timed capture instead of a manual one,
`--sample-rate` and `--threshold` if §1.2's defaults do not suit,
`--channels NAME=INDEX,…` if the probes are not on D0–D4 in plan order,
`--list-devices` when more than one Logic is attached, and `--simulate` to
fabricate the capture and exercise everything downstream of it.

### Export raw, not analyzer output

**Export the raw digital data as CSV.** Not the SPI analyzer's export. The
tools decode SPI themselves, for two reasons: the analyzer export schema
changes between Logic 2 releases, while the raw table has been a stable "time
column, then one column per channel"; and decoding here means the *measured*
mode can be reported independently of any mode an analyzer was configured
with. Both the change-based and uniformly-sampled raw exports parse.

Channels are matched by name (`SCLK`, `MOSI`, `MISO`, `CE0`, `CE1` — see
§1.2), falling back to `D0`-style short names or bare indices. `capture_runner.py`
relabels the exported header to those names from its `--channels` map, so a
capture taken through the runner needs no channel arguments downstream; a CSV
exported by hand from the GUI needs either named channels in Logic 2 or explicit
`--sclk`/`--mosi`/`--miso`/`--cs` flags.

### What it measures

- **Chip select activity** — edge count and assert windows. Zero edges is
  reported explicitly as `DEAD - never moves`, which is the MR 1 baseline.
- **Framing** — transactions are cut on CS when CS toggles, and on clock
  bursts when it does not, so a capture with a dead chip select still decodes.
  That is what lets MR 1 show the data was on the bus all along.
- **CPOL** from the clock idle level, **CPHA** from whether the data line
  moves on leading or trailing edges, and the resulting **mode**.
- **Clock rate**, median period across the transaction.
- **Decoded MOSI and MISO**, and whether they match — the MR 3 wire oracle.

### Asserting rather than eyeballing

Every `--expect-*` flag turns a measurement into a check. Exit status is 0 when
all expectations hold, 1 when one fails, 2 when the capture cannot be read, so
an agent can gate on it:

```bash
# MR 1: chip select dead on the baseline, data nonetheless present
analyze_capture.py mr1-b0.csv --cs CE0 --expect-cs-edges 0 --expect-mosi aa55aa55
# MR 1: chip select framing the transfer after the fix
analyze_capture.py mr1-b1.csv --cs CE0 --expect-cs-edges 2 --expect-mosi aa55aa55
# MR 2: mode 3 at 4 MHz was requested
analyze_capture.py t2.csv --cs CE0 --expect-mode 3 --expect-hz 4e6
# MR 3: the bytes were on the wire even where the driver returned garbage
analyze_capture.py mr3-100k.csv --cs CE0 --expect-loopback --expect-hz 100e3 --hz-tolerance 0.2
```

Widen `--hz-tolerance` at low rates: the SSP divisor is an integer, so the
achieved rate drifts from the requested one, and below roughly 25 kHz it
clamps outright.

### Producing the report

`capture_runner.py` has already written an `analysis.json` into each run
directory, so the table is one command:

```bash
compare_runs.py runs/mr1-b0/analysis.json runs/mr1-b1/analysis.json \
                --labels B0 B1 --title "MR 1 - chip select" \
                --provenance B0-master B1-cs-apl03
```

Take the `--provenance` strings from the `build_id` field of each run's
`run.json`, so the table names the builds it came from. If you are analysing a
CSV that did not come through the runner, produce the JSON first:

```bash
analyze_capture.py mr1-b0.csv --cs CE0 --json b0.json --quiet
```

Which yields, from the synthetic fixtures:

```
| Measurement | B0 | B1 | |
|---|---|---|---|
| Chip select edges | 0 | 2 | **changed** |
| Framing used | clock-burst | chip-select | **changed** |
```

Rows that are identical in both runs are hidden unless you pass `--all-rows`,
so the table shows what the fix changed and nothing else.

### Figures for the issue and the MR

The automation API exports data and nothing graphical — there is no screenshot,
image or viewport call anywhere in it — so figures are rendered by
`plot_capture.py`:

```bash
plot_capture.py runs/mr1-b0/digital.csv runs/mr1-b1/digital.csv \
    --out figs/mr1.svg --cs CE0 --labels "master" "with the fix" \
    --title "UP 4000: SPI chip select across an 8-byte transfer" \
    --footer "$(cat runs/mr1-b0/build.txt) vs $(cat runs/mr1-b1/build.txt)"
```

Two captures stack into one before/after figure, which is the shape an issue
wants. The panel captions are not typed in — they are the same measurements
`analyze_capture.py` asserts on, read from the same decoder, so the picture and
the claim cannot drift apart. Chip-select assert windows are shaded, and a
channel that never moves says so on its own trace, which is the MR 1 story in
one glance.

Output is SVG: no dependencies, crisp at any zoom, a few tens of kB, and
rendered by GitHub when committed to a repository. Add `--png PATH` for a
drag-and-drop attachment; it converts with `rsvg-convert`, `cairosvg`,
`inkscape`, ImageMagick or a headless Chrome, whichever is installed.

Two worked examples, rendered from synthetic fixtures, are committed next to the
tool so you can see the output before the bench exists:
[`examples/mr1-chip-select.svg`](analysis/examples/mr1-chip-select.svg) and
[`examples/mr2-mode-and-clock.svg`](analysis/examples/mr2-mode-and-clock.svg).
Both carry a footer saying they are fixtures. Never let a synthetic figure
travel as evidence.

At 4 MHz the clock is dense enough that CPOL and CPHA are hard to see at full
width. Pass `--window START END` (seconds) to zoom to a byte or two when the
figure needs to show the sampling edge rather than the whole transfer.

### Validate the pipeline before the bench

```bash
cd docs/testing/analysis && python3 test_analysis.py
```

64 checks over synthetic captures covering all four SPI modes, a dead chip
select, the stale-mode scenarios from §6, the MR 3 loopback sweep, the runner's
column labelling, the figure renderer, and malformed input. Run it before the
bench session: if it passes, a surprising result on real hardware is evidence
about the driver rather than about the analysis code.

You can also rehearse a full MR with no hardware at all. `--simulate` replaces
the Logic with a synthetic capture and leaves everything downstream real, so
the exports, the labelling, the expectations, the exit statuses and the
comparison table all run:

```bash
python3 capture_runner.py --out /tmp/demo/b0 --label demo-b0 \
    --simulate mode=0,hz=1e6,bytes=aa55aa55,cs-dead --expect-cs-edges 0
python3 capture_runner.py --out /tmp/demo/b1 --label demo-b1 \
    --simulate mode=0,hz=1e6,bytes=aa55aa55 --expect-cs-edges 2
compare_runs.py /tmp/demo/b0/analysis.json /tmp/demo/b1/analysis.json --labels B0 B1
```

Both exit 0; swap either expectation and that run exits 1 and names the
mismatch. Do this once before the target is even powered, so the first real
capture is not also the first time this code has executed.

---

## 10. Final integration pass

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

## 11. Reporting

Suggested artifact layout on the laptop:

```
captures/
  mr1/{B0,B1}/{ce0,ce1}.sal + .csv + .json
  mr2/{B1,B2}/t{1..5}.sal + .csv + .json
  mr3/{B1,B3}/{4M,1M,500k,200k,100k,50k,25k}.sal + .csv + .json
  mr4/parsechangelog-{master,fix}.txt
logs/
  <build>-srcversion.txt      # proof of which build produced each capture
  <build>-dmesg.txt
  <build>-spi_case/*.txt      # the RX buffers, which are the MR 3 oracle
```

Keep the `.csv` (raw export), the `.json` (`analyze_capture.py --json`) and the
`.sal` for each capture. The JSON is what `compare_runs.py` turns into the
report tables; the CSV lets a reviewer re-derive it; the `.sal` lets them open
the waveform.

Final report: one table per MR, generated by `compare_runs.py` (§9), each row
citing the capture it came from. Record `/etc/up-testbuild` (git SHA) and both
`srcversion` values alongside every capture set and pass them to
`--provenance` — a capture without build provenance proves nothing.

---

## 12. Known risks

1. **NULL pad registers.** Covered by the §4.2 pre-flight. If the gpiochip has
   fewer than 28 lines, MR 1 will oops at probe and needs a guard first.
2. **`protos.c` is modified by the build.** Always `git checkout -f`. §4.3.
3. **DKMS shadowing.** Remove the packaged version once, §4.1, or you cannot
   trust which module is loaded.
4. **ACPI overlays are separate from the modules.** They come from
   `make setup`, not from the `.ko` files. Removing the deb without re-running
   setup leaves you with no spidev nodes at all.
5. **CS-edge triggers hang the MR 1 baseline.** Timed captures only. §3.
6. **Nothing here is compile-tested yet.** The four branches were written
   without kernel headers available. Expect to fix build errors on first
   contact, and treat a clean `make` as the real first test.
