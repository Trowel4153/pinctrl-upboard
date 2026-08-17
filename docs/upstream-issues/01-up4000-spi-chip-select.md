# SPI chip selects are never driven on UP 4000 (UP-APL03)

**Labels:** bug, pinctrl, UP 4000

## Summary

`upboard_set_cs()` is a no-op on UP 4000 because `cs_pins[]` is never
populated for `BOARD_UP_APL03`. SPI transfers clock data out of the HAT header
with neither CE0 nor CE1 ever changing state, so no slave device is ever
selected or deselected — measured on hardware, both lines sit at a constant
logic 0, which for an active-low select means every device is addressed at
once.

This is a regression from c026c3e ("fix cs pins setting"), which removed the
`default:` case that used to cover this board.

## Affected

| | |
|---|---|
| Board | UP 4000, DMI board name `UP-APL03`, `BOARD_UP_APL03` (id 9) |
| File | `files/pinctrl-upboard.c` |
| Introduced in | c026c3e |
| Also affects | `BOARD_UPN_EHL01`, `BOARD_UP_ISH`, `BOARD_AIOT_IP6801` — see below |

## The code

The switch at the end of `upboard_pinctrl_probe()` assigns `cs_pins[]`
([`files/pinctrl-upboard.c#L1690-L1720`](https://github.com/up-division/pinctrl-upboard/blob/d626a5e/files/pinctrl-upboard.c#L1690-L1720),
line numbers as of `d626a5e`):

```c
	switch(pctrl->ident)
	{
	        case BOARD_UP_APL01:
	        case BOARD_UPN_APL:
                cs_pins[0].cs = &pctrl->pins[18];
                ...
	        break;
		case BOARD_UP_WHL01:
		case BOARD_UPX_WHLite:
		...
		case BOARD_UPV_PTL01:
                cs_pins[0].cs = &pctrl->pins[21];
                cs_pins[0].val = readl(pctrl->pins[21].regs);
                cs_pins[1].cs = &pctrl->pins[22];
                cs_pins[1].val = readl(pctrl->pins[22].regs);
                ...
                break;
	}
```

`BOARD_UP_APL03` appears in neither group, so `cs_pins[0].cs` and
`cs_pins[1].cs` stay `NULL`. `upboard_set_cs()` then returns immediately
([`#L612-L621`](https://github.com/up-division/pinctrl-upboard/blob/d626a5e/files/pinctrl-upboard.c#L612-L621)):

```c
inline void upboard_set_cs(u8 cs,bool level)
{
        if(cs_pins[cs].cs==NULL)
                return;
```

`spi-pxa2xx.c` installs `up_set_cs()` as the controller's `->set_cs`, and that
calls `upboard_set_cs()`, so chip select handling silently does nothing. There
is no error message — the early return is the only symptom.

## Why this is a regression

Before c026c3e the same switch ended in a `default:` case that assigned
`pins[21]` / `pins[22]`, which caught `BOARD_UP_APL03`. That commit folded the
PWM registration into the second group and deleted the `default:` label:

```diff
-		default:
-                //set cs pin
                 cs_pins[0].cs = &pctrl->pins[21];
```

Removing the fallthrough was probably deliberate — UP Core and UP Core Plus also
landed in it, and their pin tables have `GPIO3`/`GPIO4` at indices 21 and 22, so
the old `default:` was driving the wrong pads on those boards. But the same
change dropped four boards that *did* want 21/22.

`BOARD_UP_APL03` falls through to `upboard_up_pinctrl_desc` /
`upboard_up_pins[]`
([`#L1588-L1592`](https://github.com/up-division/pinctrl-upboard/blob/d626a5e/files/pinctrl-upboard.c#L1588-L1592)),
whose indices 21 and 22 are `SPI_CS0` and `SPI_CS1`
([`#L244-L245`](https://github.com/up-division/pinctrl-upboard/blob/d626a5e/files/pinctrl-upboard.c#L244-L245)),
so those are the right indices for this board.

## How this shows up

Any SPI transfer through `/dev/spidev*` on the 40-pin header: SCLK and MOSI are
active on header pins 23 and 19, while header pins 24 (CE0) and 26 (CE1) never
move for the whole transfer.

## Measured on a UP 4000

8-byte transfers at 1 MHz on UP-APL03 / kernel 7.0.0-22-generic, captured at
100 MS/s:

| | master | with the patch |
|---|---|---|
| CE0 transitions across the transfer | **0** | 2 |
| CE1 transitions across the transfer | **0** | 2 |
| Level CE0/CE1 hold when not transitioning | **constant 0** | 1 between transfers |

The level is the part worth reading twice. Both selects sit at a **constant
logic 0** for the entire capture, and these are active-low selects — so they
are not inert, they are stuck **asserted**. Every device on the bus is
addressed, permanently and simultaneously, while the bus clocks data. On a
two-device bus both slaves would drive MISO at once.

A logic capture cannot distinguish a line driven low from an undriven line the
analyser reads as low, so this is a statement about the level, not about which
of the two it is. Either way nothing on the header ever sees a select edge.

<!-- Fill in before filing: attach figs/mr1-chip-select.svg from the results
     directory. The panel captions come from the decoder, so the edge count and
     the stuck level are measured rather than typed. -->

## Reproduce

```bash
cat /sys/class/dmi/id/board_name          # UP-APL03
# probe header pin 24 (CE0) and pin 23 (SCLK) with a logic analyser
python3 - <<'EOF'
import spidev
s = spidev.SpiDev(); s.open(1, 0)
s.mode = 0; s.max_speed_hz = 1000000
s.xfer2([0xaa, 0x55] * 4)
EOF
# SCLK toggles; CE0 does not move
```

## Suggested fix

Add a case for this board rather than restoring the `default:`, so UP Core and
UP Core Plus keep the behaviour c026c3e gave them:

```diff
                 cs_pins[1].cs = &pctrl->pins[17];
                 cs_pins[1].val = readl(pctrl->pins[17].regs);
 	        break;
+	        case BOARD_UP_APL03:
+                cs_pins[0].cs = &pctrl->pins[21];
+                cs_pins[0].val = readl(pctrl->pins[21].regs);
+                cs_pins[1].cs = &pctrl->pins[22];
+                cs_pins[1].val = readl(pctrl->pins[22].regs);
+	        break;
 		case BOARD_UP_WHL01:
```

Deliberately a separate case and not an addition to the second group: that group
also calls `upboard_pwm_register()`, and the pre-c026c3e `default:` gave these
boards chip selects *without* PWM. Restoring CS alone is the faithful,
minimal restoration; adding UP 4000 to the PWM group would be a new behaviour
change.

## Questions for the maintainers

1. **The other three boards.** `BOARD_UPN_EHL01`, `BOARD_UP_ISH` and
   `BOARD_AIOT_IP6801` also lost their chip selects in c026c3e and also use
   `upboard_up_pins[]`. They look like they want the same one-line case, but we
   have no hardware to confirm. Should they be added in the same change?
2. **PWM on UP 4000.** Is `upboard_pwm_register(0)` wanted on this board? The
   history says no — before c026c3e, PWM registration sat in its own cases with
   their own `break`, so boards reaching `default:` got CS only. Happy to add it
   if that was an oversight rather than intent.
3. **Bounds checking.** `pctrl->pins[21].regs` is only assigned for
   `i < descs->ndescs` in `upboard_acpi_get_pins()`. On a board whose ACPI
   `external-gpios` list is shorter than the pin table, this `readl()`
   dereferences `NULL` at probe. Worth a guard independently of this fix?
