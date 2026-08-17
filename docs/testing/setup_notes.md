Execute docs/testing/up4000-spi-test-plan.md. I've completed §1. The configuration you need should be below. The target is the SSH host up4000; passwordless sudo is set up. Logic 2's automation server is enabled; drive it with docs/testing/analysis/capture_runner.py. Section 4.1 one-time prep is not done yet — start there. Do the §4.2 pre-flight before building anything with MR 1 in it, and stop and ask me if the gpiochip does not report 28 lines. Work through MR 1, then MR 2, then MR 3, capturing baseline and fix for each, and give me the §11 report at the end. I will be at the console for the first B1 boot — tell me before you reboot into it.

| Config Item                                 | Value                                                |
|---------------------------------------------|------------------------------------------------------|
| SSH alias for the target                    | `up4000`                                             |
| Capture output directory (Windows path)     | `C:\Users\tvlowery\Downloads\202160817_spi_test`     |
| Capture output directory (WSL path)         | `/mnt/c/Users/tvlowery/Downloads/202160817_spi_test` |
| Saleae automation port                      | `10430`                                              |
| Saleae MCP server port                      | `10530`                                              |
| Python interpreter with `logic2-automation` | `/home/tvlowery/r5/pinctrl-upboard/venv`             |
| Repo checkout on the laptop                 | `/home/tvlowery/r5/pinctrl-upboard`                  |

# Setup Notes

## 1.1 Bench, physical
* Plugged everything in:

  |     Pin    |     Channel     |     Description                    |
  |------------|-----------------|------------------------------------|
  |     1      |     3           |     3.3 V                          |
  |     6      |     0 ground    |     Ground                         |
  |     19     |     0           |     MISO (jumpered to   pin 21)    |
  |     21     |     0           |     MOSI (jumpered to   pin 19)    |
  |     23     |     2           |     CLK                            |
  |     24     |     4           |     CS0                            |
  |     26     |     5           |     CS1                            |
* Took a photo

## 1.2 Logic 2, in the GUI
* Enabled Logic automation server
* Configured pins, logic level, and sample rate
* Chose
	
## 1.3 Claude Code and the automation API, on the laptop
* Claude already installed in WSL
* Cloned repo
* Created venv in repo and installed logic2-automation
* Already in mirrored networking mode
* Analysis self-tests pass
* Simulated case executes

## 1.4 Credentials — the parts that need a password
* Used ssh-copy-id to copy key to UP Board
* Added .ssh/config entry
* Can connect
* Enabled passwordless sudo
* Installed pacakges

## 1.5 Recovery plan — the one thing an agent cannot do for you
* I can do that stuff

## 1.6 Handoff smoke test
* Ran
	```
	ssh -o BatchMode=yes up4000 \
	  'sudo true && echo SUDO_OK; cat /sys/class/dmi/id/board_name; uname -r'
	SUDO_OK
	UP-APL03
	7.0.0-22-generic
	```

* Ran
	```
	ssh up4000 sudo reboot
	until ssh -o ConnectTimeout=5 -o BatchMode=yes up4000 true 2>/dev/null; do sleep 5; done
	echo "target came back"
	target came back
	```

* That returned immediately. Board didn't reboot.
* Looks like a race condition. Board is reachable before reboot begins. Works as expected with some time between the reboot and the wait.
* Tried again and the board got stuck in the reboot. I pulled power. Wait worked as expected. I can watch the console and pull power if the board fails to reboot.