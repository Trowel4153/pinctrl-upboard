# SPI transfers under 32 bytes ignore the requested mode and clock rate

**Labels:** bug, spi

## Summary

`up_spi_transfer()` writes `SSCR0` with `SSCR0_SSE` already set and writes
`SSCR1` afterwards. The PXA SSP only latches `SCR`/`DSS` (SSCR0) and
`SPO`/`SPH` (SSCR1) while the port is disabled, so neither the clock divisor nor
CPOL/CPHA takes effect. Every transfer shorter than 32 bytes runs at whatever
mode and speed were latched by the first transfer after probe.

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

1. `SSCR0_SSE` is OR-ed in, so the write that is supposed to change `SCR` (the
   clock divisor) and `DSS` happens with the port enabled — the new divisor is
   not latched.
2. `SSCR1`, which carries `SPO` and `SPH`, is written *after* `SSCR0` has
   re-enabled the port, so CPOL/CPHA are not latched either.

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

## How this shows up

The failure is stateful, which makes it easy to misread as intermittent: the
first short transfer after probe latches a mode and a rate, and every later
short transfer inherits them regardless of what it asked for. Two userspace
programs that each work in isolation will break when run in sequence, and the
same program will behave differently depending on what ran before it.

A short transfer requesting mode 3 at 4 MHz after a mode 0 / 1 MHz transfer
comes out on the wire as mode 0 at 1 MHz. Nothing reports an error.

Transfers of 32 bytes and over are unaffected — they take
`pxa2xx_spi_transfer_one()`, so `spidev` users who only ever send long buffers
will not see this.

<!-- Fill in before filing: capture showing the requested vs measured
     mode/rate for the same ioctl sequence, before and after the patch. -->

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
+	 * SCR/DSS in SSCR0 and SPO/SPH in SSCR1 are only latched while the
+	 * SSP is disabled, so program them with SSE clear.  Writing SSCR0
+	 * with SSE already set leaves the controller running at whatever
+	 * speed and mode the previous transfer latched.
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
