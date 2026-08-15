#!/usr/bin/env python3
"""Run one SPI transfer with an explicit mode, speed and length.

Test harness for the UP 4000 SPI driver fixes.  Each invocation performs a
single spi_ioc_transfer so that the requested mode/speed/length map one to one
onto what the driver sees, which is what the polled-path tests depend on.

Transfers shorter than 32 bytes take the driver's polled up_spi_transfer()
path; 32 bytes and over take the long path.  --len selects which.

Exit status:
  0  transfer completed and RX == TX  (loopback match)
  1  transfer completed and RX != TX  (loopback mismatch)
  2  the ioctl itself failed, e.g. ETIMEDOUT

With MOSI and MISO jumpered (header pins 19 and 21) the exit status is
meaningful.  Without the jumper only the printed values are.
"""

import argparse
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bus", type=int, default=1,
                    help="SPI bus number; discover with ls /sys/bus/spi/devices/")
    ap.add_argument("--dev", type=int, default=0,
                    help="chip select: 0 = CE0/header pin 24, 1 = CE1/header pin 26")
    ap.add_argument("--mode", type=int, default=0, choices=[0, 1, 2, 3])
    ap.add_argument("--speed", type=int, default=1000000, help="Hz")
    ap.add_argument("--len", type=int, default=8, dest="length",
                    help="transfer length in bytes; <32 uses the polled path")
    ap.add_argument("--pattern", default="aa55",
                    help="hex byte pattern, repeated to fill --len")
    ap.add_argument("--label", default="", help="tag for the output line")
    args = ap.parse_args()

    try:
        import spidev
    except ImportError:
        sys.exit("python3-spidev is not installed: sudo apt install python3-spidev")

    pattern = bytes.fromhex(args.pattern)
    if not pattern:
        sys.exit("--pattern must contain at least one byte")
    tx = (pattern * (args.length // len(pattern) + 1))[:args.length]

    spi = spidev.SpiDev()
    spi.open(args.bus, args.dev)
    # Assigning .mode issues SPI_IOC_WR_MODE, which runs spi_setup() and
    # recomputes the controller's cr1.  Whether that reaches the hardware on
    # the next short transfer is exactly what the SSCR latch fix decides.
    spi.mode = args.mode
    spi.max_speed_hz = args.speed
    spi.bits_per_word = 8

    err = None
    rx = b""
    try:
        rx = bytes(spi.xfer2(list(tx), args.speed, 0, 8))
    except OSError as exc:
        err = exc
    finally:
        spi.close()

    print(f"label={args.label or '-'} bus={args.bus}.{args.dev} "
          f"mode={args.mode} speed={args.speed} len={args.length} "
          f"path={'polled' if args.length < 32 else 'long'}")
    print(f"tx={tx.hex()}")

    if err is not None:
        print(f"rx=<ioctl failed> errno={err.errno} ({err.strerror})")
        print("result=ERROR")
        return 2

    print(f"rx={rx.hex()}")
    match = rx == tx
    print("result=" + ("MATCH" if match else "MISMATCH"))
    return 0 if match else 1


if __name__ == "__main__":
    sys.exit(main())
