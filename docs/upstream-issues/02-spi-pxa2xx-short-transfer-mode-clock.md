# SPI transfers under 32 bytes ignore the requested mode and clock rate

**Labels:** bug, spi

## Summary

`up_spi_transfer()` reprograms `SSCR0` and `SSCR1` without ever clearing
`SSCR0_SSE`. Intel documents that these fields only take effect while the port
is disabled — in as many words for `SPO`/`SPH` and for `DSS`, and via a blanket
rule for the rest of `SSCR0` (quoted under
[What the documentation says](#what-the-documentation-says) below). So a short
transfer runs with whatever mode and clock rate were latched earlier rather
than the ones it asked for.

This is not a transient: in this tree the SSP is never disabled between
messages, because `unprepare_transfer_hardware` is commented out. Once the
first transfer after probe sets `SSE`, it stays set for the life of the module,
so every later short transfer reconfigures a running port.

The DMA/interrupt path in the same file already does this correctly. This is
about the polled path only.

## Affected

| | |
|---|---|
| File | `files/spi-pxa2xx.c`, `up_spi_transfer()` |
| Trigger | any transfer with `len < 32` |
| Symptom | requested `SPI_IOC_WR_MODE` and `max_speed_hz` silently ignored |

## The code

Transfers under 32 bytes are routed to the polled path
([`files/spi-pxa2xx.c#L1075-L1083`](https://github.com/up-division/pinctrl-upboard/blob/d626a5e/files/spi-pxa2xx.c#L1075-L1083)):

```c
	//improve small data transfer,32+ len using DMA tansfer
	if (transfer->len < 32)
	{
		...
		return up_spi_transfer(drv_data, chip, transfer);
	}
```

which programs the registers like this
([`#L1005-L1011`](https://github.com/up-division/pinctrl-upboard/blob/d626a5e/files/spi-pxa2xx.c#L1005-L1011)):

```c
	//SSCR0
	pxa2xx_spi_write(drv_data, SSCR0, pxa2xx_configure_sscr0(drv_data,
	clk_div, transfer->bits_per_word) | SSCR0_SSE );
	//SSCR1
	pxa2xx_spi_write(drv_data, SSCR1, chip->cr1 );
```

Two problems in three lines:

1. `SSCR0_SSE` is OR-ed in rather than the port being stopped first. On every
   transfer after the first, `SSE` is already set, so this is a reconfiguration
   of `SCR` (the clock divisor) and `DSS` on a running port.
2. `SSCR1`, which carries `SPO` and `SPH`, is written *after* that `SSCR0`
   write, so the port is unambiguously enabled by then — CPOL and CPHA are
   never written with `SSE` clear, not even on the first transfer.

Compare `pxa2xx_spi_transfer_one()`, which gets the same job right
([`#L1183-L1197`](https://github.com/up-division/pinctrl-upboard/blob/d626a5e/files/spi-pxa2xx.c#L1183-L1197)):

```c
	/* Stop the SSP */
	if (!is_mmp2_ssp(drv_data))
		pxa_ssp_disable(drv_data->ssp);
	...
	/* First set CR1 without interrupt and service enables */
	pxa2xx_spi_update(drv_data, SSCR1, change_mask, cr1);
	/* See if we need to reload the configuration registers */
	pxa2xx_spi_update(drv_data, SSCR0, GENMASK(31, 0), cr0);
	/* Restart the SSP */
	pxa_ssp_enable(drv_data->ssp);
```

## Why the port is already enabled

Writing `SSCR0` and `SSE` together is legal *when the port is currently
disabled* — Intel says so explicitly (quoted below). So this code would be
correct if `SSE` were clear on entry. It is not, because this tree never clears
it on the normal path:

```c
	//controller->unprepare_transfer_hardware = pxa2xx_spi_unprepare_transfer;
```

That line is commented out in `pxa2xx_spi_probe()`, where mainline sets it, so
`pxa2xx_spi_off()` — the only thing that clears `SSE` outside error and suspend
paths — is never reached. The short path also sets
`controller->auto_runtime_pm = false`, and runtime suspend only gates the clock
rather than clearing `SSE`.

The result: the first transfer after probe finds `SSE` clear (probe leaves it
that way) and latches `SSCR0` correctly; from then on `SSE` stays set for the
life of the module and every short transfer is reconfiguring a running port.

## What the documentation says

The SSP in this controller is the PXA SSP, and Intel's PXA documentation states
the constraint directly. From the [Intel® PXA27x Processor Family Developer's
Manual](https://support.eurotech-inc.com/developers/datasheets/PXA270_DeveloperManual.pdf)
(order number 280000-001, April 2004), §8.5 "Register Descriptions", p. 8-23:

> For SSCR0_x, the DSS, FRF, EDSS, ECS, NCS, and ACS bit fields must be written
> before the SSE bit is set. If the DSS, FRF, EDSS, ECS, NCS, or ACS bits need
> to be modified after the SSE bit is set; clear the SSE bit, make the
> modifications, and then again set the SSE bit.

and on p. 8-24, which is the one that matters most here because `SPO` and `SPH`
*are* CPOL and CPHA:

> For SSCR1_x, only the SPO, SPH, and SCFR bits must be written before the SSE
> bit is set. If the SPO, SPH, or SCFR bits need to be modified after the SSE
> bit is set; clear the SSE bit, make the modifications, and then again set the
> SSE bit.

The `SSE` bit description itself (Table 8-6, p. 8-27) gives the blanket rule,
and the sentence that makes the combined write legal only from a disabled port:

> Also, SSE must be cleared before re-configuring the SSCR0_x, SSCR1_x, or
> SSPSP_x registers; any or all control bits in SSCR0_x can be written at the
> same time as the SSE.

The same sentence appears verbatim in the Intel® PXA255 Processor Developer's
Manual, Table 16-3, p. 16-19.

**One caveat, stated so it does not have to be discovered in review.** `SCR` is
not in the p. 8-23 enumeration; it is covered only by the blanket `SSE`
sentence above. Its own bit description (Table 8-6, p. 8-26) points the other
way for a different scenario:

> NOTE: Software must not change SCR when SSPSCLKx is enabled (through use of
> the SSPSCLKEN pin or SSPCR1_x[ECRA] or SPCR1_x[ECRB]) because doing so causes
> the SSPSCLKx frequency to immediately change.

That note is about the clock-enable input, not about `SSE`, but it does mean
the documented consequence for `SCR` specifically is less clear-cut than for
`SPO`/`SPH`. In the event this did not matter: both halves were measured, and
they fail in different ways on the same transfer — see below.

We have not found a public register-level document for the Apollo Lake LPSS
SSP; Intel's [E3900/A3900 datasheet
addendum](https://cdrdv2-public.intel.com/336256/336256-intel-atom-processor-e3900-and-a3900-series-datasheet-addendum-rev004.pdf)
documents the SIO/LPSS SPI *signals* only. What we can say is that mainline
applies the disable/reconfigure/enable sequence to every `ssp_type` including
`LPSS_BXT_SSP`, which is what this board probes as, with MMP2 the sole
exception.

## Measured on a UP 4000

Five 8-byte transfers in sequence on UP-APL03 / kernel 7.0.0-22-generic, no
reload between them, captured at 100 MS/s. T3 is 40 bytes, so it takes the long
path and acts as the control.

| Case | Path | Requested | Measured on master | With the patch |
|---|---|---|---|---|
| T1 | polled | mode 0, 1 MHz | mode 0, 1.000 MHz ✓ | mode 0, 1.000 MHz ✓ |
| T2 | polled | mode 3, 4 MHz | **mode 0, 1.000 MHz** | mode 3, 3.846 MHz ✓ |
| T3 | long | mode 3, 4 MHz | mode 3, 3.846 MHz ✓ | mode 3, 3.846 MHz ✓ |
| T4 | polled | mode 3, 4 MHz | **mode 0**, 3.846 MHz | mode 3, 3.846 MHz ✓ |
| T5 | polled | mode 0, 1 MHz | mode 0, **3.846 MHz** | mode 0, 1.000 MHz ✓ |

`3.846 MHz` is `100 MHz / 26`, the integer-divisor rendering of a 4 MHz
request.

**T4 is the observation that pins the mechanism down.** T3 immediately before
it is a long-path transfer, which takes the disable/reconfigure/enable route
and genuinely puts mode 3 at 4 MHz on the wire. T4 then asks for *the same
mode 3 at the same 4 MHz* on the polled path — and comes back at 3.846 MHz in
**mode 0**. The clock divisor survived; the polarity did not.

That asymmetry is hard to explain any other way. It is not "the request was
never applied", because half of the same request was. It is what the manual
describes: `SSCR0` and `SSCR1` written to a port with `SSE` set, where the
divisor happens to take and the `SPO`/`SPH` fields do not.

T2 and T5 are the stateful demonstration for the clock alone: the same request
produces different wire behaviour depending only on what ran before it.

<!-- Fill in before filing: attach figs/mr2-mode-and-clock.svg from the
     results directory (T2, master vs patched).

One methodology note worth carrying into the issue if anyone asks how CPOL was
read: this controller parks SCLK low between messages, so a mode-3 transfer
opens with a setup rise inside the chip-select window.  Reading CPOL from the
level *before* the first edge in the window gets it backwards.  Read it from
the level after the last clock edge of the frame.  Our first analysis pass got
this wrong and briefly concluded mode was unmeasurable on the bench. -->

## How this shows up

The two halves fail differently, which is worth knowing before you go looking
for it.

**The clock rate is stateful**, and that makes it easy to misread as
intermittent: a short transfer runs at whatever divisor was last latched, so
two userspace programs that each work in isolation break when run in sequence,
and the same program behaves differently depending on what ran before it.

**The mode is not stateful — it is simply never set.** On the polled path the
wire stayed at CPOL=0 in every case we measured, including immediately after a
long-path transfer had put CPOL=1 on it. A program that only ever issues short
transfers gets mode 0 no matter what it asks for, consistently, which is if
anything easier to miss: it looks like the device just does not work rather
than like a configuration bug.

Nothing reports an error in either case.

Transfers of 32 bytes and over are unaffected — they take
`pxa2xx_spi_transfer_one()`, so `spidev` users who only ever send long buffers
will not see this.

## Reproduce

Probe SCLK (header pin 23) and MOSI (pin 19), then run these in one process, in
order:

```python
import spidev
s = spidev.SpiDev(); s.open(1, 0)

s.mode = 0; s.max_speed_hz = 1_000_000       # T1: latches mode 0 / 1 MHz
s.xfer2([0xaa, 0x55] * 4)

s.mode = 3; s.max_speed_hz = 4_000_000       # T2: asks for mode 3 / 4 MHz
s.xfer2([0xaa, 0x55] * 4)                    #     wire still shows mode 0 / 1 MHz
```

T2 is an eight-byte transfer, so it takes the polled path. Measure CPOL, CPHA
and the clock period on the analyser rather than decoding the payload — a
pattern held for a full clock period decodes identically under all four modes,
so the bytes will look correct even when the mode is wrong.

## Suggested fix

Bracket the register writes with disable/enable and put `SSCR1` first, matching
the long path:

```diff
-	//SSCR0
-	pxa2xx_spi_write(drv_data, SSCR0, pxa2xx_configure_sscr0(drv_data,
-	clk_div, transfer->bits_per_word) | SSCR0_SSE );
+	/*
+	 * DSS in SSCR0 and SPO/SPH in SSCR1 must be written with the port
+	 * disabled to take effect, so clear SSE before programming them.
+	 * Reconfiguring a running port leaves the controller at whatever
+	 * mode and speed the previous transfer latched.
+	 */
+	/* On MMP, disabling SSE seems to corrupt the Rx FIFO */
+	if (!is_mmp2_ssp(drv_data))
+		pxa_ssp_disable(drv_data->ssp);
 	//SSCR1
 	pxa2xx_spi_write(drv_data, SSCR1, chip->cr1 );
-
+	//SSCR0
+	pxa2xx_spi_write(drv_data, SSCR0, pxa2xx_configure_sscr0(drv_data,
+	clk_div, transfer->bits_per_word) );
+	pxa_ssp_enable(drv_data->ssp);
```

`pxa_ssp_enable()` sets `SSCR0_SSE`, so dropping the manual OR is intentional
rather than an omission. The `is_mmp2_ssp()` guard mirrors the existing comment
on the long path about disabling SSE corrupting the Rx FIFO on MMP.
