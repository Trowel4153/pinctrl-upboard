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

## Measured on a UP 4000

8-byte loopback transfers of `deadbeefdeadbeef`, swept by clock rate on
UP-APL03 / kernel 7.0.0-22-generic, captured at 100 MS/s. Each speed is primed
with a long-path transfer first, so the polled path is genuinely running at the
requested rate.

| Requested | `xfer2()` returned | Driver reported | Clock edges after CS released |
|---|---|---|---|
| 4 MHz, 1 MHz, 500 kHz, 200 kHz | `deadbeefdeadbeef` | success | 0 |
| 100 kHz | **`00de00adbe00ef00`** | success | **49 of 128** |
| 50 kHz | **`00000000000000de`** | success | **113 of 128** |
| 25 kHz | **`0000000000000000`** | success | **127 of 128** |

The interleaved zeros are the signature: `SSDR` read before the word arrived,
returned as data, with no error at any layer.

**The wire is torn too, and this is the part worth acting on.** The controller
releases chip select while the SSP is still shifting. At 100 kHz the frame
closes with 49 of the transfer's 128 clock edges still to come; at 25 kHz it
closes before the second clock edge. A loopback jumper is forgiving enough not
to care, but a real slave sees the transaction end mid-word — which can leave
it desynchronised for subsequent transfers, not merely return one bad buffer.

So this is not only "the driver gives up reading too early". The frame the
driver emits is invalid.

After the patch every speed down to 25 kHz returns the full payload and the
clock stops inside the frame. Across all 28 captures in the sweep the two
oracles agree exactly: zero clock edges past the frame on each of the 25 that
returned correct data, non-zero on each of the 3 that did not.

<!-- Fill in before filing: attach figs/mr3-rx-loopback.svg from the results
     directory, and quote the target's rx= line from run.json beside it.

Note for whoever files this: the timeout path was never exercised. The widened
bound was sufficient at every speed tested, so -ETIMEDOUT did not fire on any
build. That bears directly on the open question below -- do not imply the
timeout behaviour was validated on hardware. -->

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

Worth saying plainly: **on our bench that path never ran.** The time-based
bound was sufficient at every speed from 4 MHz down to 25 kHz, so every
post-patch transfer succeeded and `-ETIMEDOUT` was never returned. The
error path is therefore reasoned about, not measured, and if you would rather
it did something else, nothing in our results argues against you.
