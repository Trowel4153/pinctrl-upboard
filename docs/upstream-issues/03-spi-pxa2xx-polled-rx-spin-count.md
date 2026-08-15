# Polled Rx wait uses a bit-rate-scaled spin count and returns stale data

**Labels:** bug, spi

## Summary

The Rx wait in `up_spi_transfer()` is bounded by a spin count derived from the
requested bit rate, `speed_hz / 1000`. The count has no relationship to how long
a word actually takes to clock out, and at low speeds it expires first. The
driver then reads `SSDR` anyway and stores whatever it holds, so the transfer
returns stale or uninitialised data and reports success.

## Affected

| | |
|---|---|
| File | `files/spi-pxa2xx.c`, `up_spi_transfer()` |
| Trigger | transfers with `len < 32` at low `speed_hz` |
| Symptom | wrong Rx bytes, no error returned |

## The code

[`files/spi-pxa2xx.c#L1022-L1026`](https://github.com/up-division/pinctrl-upboard/blob/d626a5e/files/spi-pxa2xx.c#L1022-L1026):

```c
	    //rx
            unsigned long limit = transfer->speed_hz/1000;
    	    while(!(read_SSSR_bits(drv_data, SSSR_RNE)) && --limit);
	    ReadVal(pxa2xx_spi_read(drv_data, SSDR), drv_data->rx);
```

Three things go wrong here:

- **The bound scales the wrong way.** Slower transfers take *longer*, but
  `speed_hz / 1000` makes the budget *smaller*. At 1 MHz the loop gets 1000
  iterations; at 100 kHz it gets 100, for a word that takes ten times as long.
  At 25 kHz it gets 25.
- **The units are meaningless.** The count is loop iterations, whose duration
  depends on CPU frequency, compiler output and MMIO read latency. Nothing ties
  it to the time an 8-bit word needs on the wire.
- **Expiry is not detected.** When `limit` reaches zero the loop exits exactly
  as it does on success, and the very next line reads `SSDR` and stores the
  result. A timeout is indistinguishable from data.

The consequence is a silent wrong answer rather than a failure: `xfer2()`
returns normally with bytes that were never received.

## How this shows up

A loopback (MOSI jumpered to MISO, header pins 19 and 21) short transfer returns
the right bytes at high clock rates and wrong bytes below some threshold, with
no error anywhere. Because the reads are stale FIFO contents, the wrong bytes
are often the *previous* transfer's data, which reads as an intermittent
off-by-one-transfer bug.

A logic analyser shows the bytes correctly on the wire in both cases — the
defect is entirely in when the driver gives up reading them.

<!-- Fill in before filing: loopback sweep across 4 MHz / 1 MHz / 100 kHz /
     25 kHz showing where the returned data diverges from the wire. -->

## Reproduce

```python
import spidev
s = spidev.SpiDev(); s.open(1, 0)
s.mode = 0
tx = [0xde, 0xad, 0xbe, 0xef]
for hz in (4_000_000, 1_000_000, 100_000, 25_000):
    s.max_speed_hz = hz
    rx = s.xfer2(list(tx), hz, 0, 8)
    print(hz, bytes(rx).hex(), "OK" if rx == tx else "MISMATCH")
```

With MOSI and MISO jumpered every line should print `OK`.

## Suggested fix

Bound the wait by wall clock time and fail the transfer instead of storing
whatever `SSDR` holds:

```diff
+/* Wall clock bound for the polled PIO transfer path */
+#define UP_XFER_TIMEOUT_MS	100
```

```diff
-	    //rx
-            unsigned long limit = transfer->speed_hz/1000;
-    	    while(!(read_SSSR_bits(drv_data, SSSR_RNE)) && --limit);
+	    /*
+	     * rx, bounded by wall clock time.  A spin count scaled by the
+	     * bit rate expires before the word has even been clocked out
+	     * at the lower speeds, and the read below then stores whatever
+	     * SSDR happens to hold.
+	     */
+            unsigned long limit = jiffies + msecs_to_jiffies(UP_XFER_TIMEOUT_MS);
+    	    while(!(read_SSSR_bits(drv_data, SSSR_RNE))) {
+		    if (time_after(jiffies, limit)) {
+			    dev_err_ratelimited(&drv_data->controller->dev,
+						"timeout waiting for Rx data\n");
+			    return -ETIMEDOUT;
+		    }
+		    cpu_relax();
+	    }
 	    ReadVal(pxa2xx_spi_read(drv_data, SSDR), drv_data->rx);
```

100 ms is far longer than any legitimate word on this path — 31 bytes at the
25 kHz floor is under 10 ms — so it only fires when the word genuinely never
arrives. `cpu_relax()` keeps the busy-wait polite.

## Question for the maintainers

Returning `-ETIMEDOUT` from `up_spi_transfer()` propagates out of
`pxa2xx_spi_transfer_one()` to the SPI core, which will fail the message rather
than call `spi_finalize_current_transfer()`. That is the intended behaviour —
a failed transfer should be visible to userspace as an error — but it is a
behaviour change from today's silent-wrong-data, so worth confirming it is the
outcome you want.
