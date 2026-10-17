# Galaxy C9 Pro (SM-C9000): fix the screen on/off loop caused by a display fault

After a screen-related hardware issue, waking this phone sometimes made the display **flash on and off
forever** (~0.25 s on, ~0.2 s off) until you locked and woke it again, often several times. Touch kept working,
and Always-On Display was fine.

This repo shows how that was fixed **without touching the hardware**: a few bytes changed in the device tree inside
the stock boot image. It worked on one SM-C9000 (TGY / Hong Kong, firmware `C9000ZHU1CSC1`): before, about
3 in 4 wakes looped; after, 18 of 18 quick wakes were normal and Always-On Display kept working. No Samsung files are included; you download the official
firmware and build the patched image yourself with the scripts here. The result is byte-identical to the one
tested (hashes in [`out/SHA256SUMS`](out/SHA256SUMS)).

> **Use v2 (`boot-v2.img`).** The first version of this patch (v1, `boot-patched.img`) also stopped the loop, but
> it makes the kernel **crash every time Always-On Display turns on or off**; with an AOD schedule that becomes a
> boot loop. If you flashed v1, turn AOD off and flash v2. Details:
> [docs/v2-aod-crash-and-fix.md](docs/v2-aod-crash-and-fix.md).

> **Warnings.** This trips **Knox** permanently (Samsung Pay, Secure Folder and Samsung Pass stop working, even
> after going back to stock). Unlocking and flashing **wipe the phone** several times. You do this at your own
> risk; read everything first. Download mode can't be damaged by a bad boot image, so a full stock flash is
> always the way back.

## Is this your problem?

Run `adb logcat -v time` while waking the phone. If you see this, repeating every ~0.5 s:

```
hwc_setPowerMode: Setting mode 2 on display: 0
getPanelResetStatus: got change event in fb0 with PANEL_ALIVE=0
hwc_setPowerMode: Setting mode 0 on display: 0
```

...then the display driver is resetting the panel because of its ESD/error interrupt. Or just run
`python3 -I scripts/wake_monitor.py` and lock/wake the phone ~20 times: it prints `OK` or `LOOP (n panel resets)` per wake.

## What's going on

The panel (Samsung S6E3FA3) has an error output (ERR_FG, GPIO 133) that the driver treats as "panel dead".
On this phone, after the hardware issue, the panel raises it ~0.3 s after many cold power-ons. The driver resets the panel, which is
another cold power-on, which raises the line again: an endless loop. In `reg_read_irq` mode the driver never even
reads the panel: the interrupt alone counts as failure.

The panel node sets `qcom,mdss-dsi-panel-status-check-mode = "reg_read_irq"`, which tells the driver to watch that
pin. **v2** removes just that property (`scripts/patch_esd.py --prop`, overwriting it with `FDT_NOP` tokens: nothing
moves, the kernel and ramdisk stay byte-identical, and the boot image header ID is recomputed). The ESD check stays
enabled, but the driver never requests the GPIO 133 interrupt and falls back to Qualcomm's standard BTA check: every
few seconds it asks the panel to answer on the DSI link, and resets it only if it really doesn't.

**v1** removed `qcom,esd-check-enabled` instead. That disables the ESD setup completely, which leaves a function
pointer NULL that Samsung's AOD code calls without checking (`mdss_samsung_panel_low_power_config`): kernel panic on
the first AOD transition. Diagnosis with logs and kernel source references:
[docs/diagnosis-and-original-plan.md](docs/diagnosis-and-original-plan.md) (the loop, v1) and
[docs/v2-aod-crash-and-fix.md](docs/v2-aod-crash-and-fix.md) (the AOD crash, v2).

**Side effect seen:** on some wakes brightness or colors are slightly off (the same fault disturbing the panel's
startup settings, now without a reset). Locking and waking again fixes it. If the panel ever *really* stops answering
after a wake, the BTA check will still reset it, so on a worse fault the loop could come back on some wakes. The real fix is repairing the display hardware (panel, flex cable or connector).

## What you need

- Linux with `heimdall`, `lz4`, `dtc`, `adb`, `python3` (Fedora: `sudo dnf install heimdall lz4 dtc android-tools`;
  Debian/Ubuntu: `sudo apt install heimdall-flash lz4 device-tree-compiler adb`). Windows/Odin works too for flashing.
- Firmware from [samfw.com](https://samfw.com/firmware/SM-C9000/TGY) (all builds there are bootloader revision 1):
  - **`C9000ZHU1CSC1`** (Android 8.0, `SAMFW.COM_SM-C9000_TGY_C9000ZHU1CSC1_fac.zip`, 2.46 GB): the firmware that gets patched.
  - **`C9000ZHU1BRC2`** (Android 7.1.1, `SAMFW.COM_SM-C9000_TGY_C9000ZHU1BRC2_fac.zip`, 2.24 GB): only for unlocking.
- **CROM Service 1.0.8** (Samsung's bootloader unlock app for China/HK models) from
  [APKMirror](https://www.apkmirror.com/apk/samsung-electronics-co-ltd/crom-service/crom-service-1-0-8-release/).
  Check it: SHA-256 `2b52bcf8d79f9a40b703ff98fa7c8ca3787ebe6c80e93086923532fa72da4d0d`, package `com.sec.android.app.kwb`,
  signing certificate "Samsung Cert" with SHA-1 `9C:A5:17:0F:38:19:19:DF:E0:44:6F:CD:AB:18:B1:9A:14:3B:31:63`.
- Back up the phone, and remove your Google account before the first wipe (Factory Reset Protection).

Another region or firmware version? The patch script works on any boot image whose device tree has the property.
Patch *the exact firmware your phone runs*, skip the hash comparison, and run every check below.

## Steps

### 1. Build the patched boot image (no phone needed)

```bash
mkdir -p fw work out
unzip -p SAMFW.COM_SM-C9000_TGY_C9000ZHU1CSC1_fac.zip 'AP_*' | tar -x -C fw boot.img.lz4
lz4 -d fw/boot.img.lz4 work/boot-stock.img
P=qcom,mdss-dsi-panel-status-check-mode
python3 -I scripts/patch_esd.py info   work/boot-stock.img --prop $P
python3 -I scripts/patch_esd.py patch  work/boot-stock.img out/boot-v2.img --prop $P
python3 -I scripts/patch_esd.py verify work/boot-stock.img out/boot-v2.img --prop $P   # must print RESULT: PASS
python3 -I scripts/patch_esd.py patch  work/boot-stock.img out/boot-patched.img        # v1, fallback only (AOD off!)
cp work/boot-stock.img out/boot-stock.img                                              # keep for rollback
scripts/make_odin_tars.sh                                                              # Odin packages + SHA256SUMS
git diff out/SHA256SUMS                                                                # empty = identical to the tested build
```

`info` should report one DTB (`Samsung C9LTE CHN HK PROJECT Rev08`, board-id (8, 8)) with two hits: the real panel
`/soc/qcom,mdss_mdp@1a00000/ss_dsi_panel_S6E3FA3_AMS598KH01_FHD` (`"reg_read_irq"`) and an unused
`qcom,mdss_dsi_hx8399a_1080p_video` node (`"reg_read"`, harmless to remove), header ID recipe `qc`, and a
`SEANDROIDENFORCE` tail. `python3 -I scripts/patch_esd.py info out/boot-v2.img` must still list `qcom,esd-check-enabled`.
Optional cross-check: `python3 -I scripts/patch_esd.py unpack` both images, decompile the `dtb_*.dtb` files with
`dtc -I dtb -O dts`, and `diff` them: only the two `qcom,mdss-dsi-panel-status-check-mode = ...;` lines differ.

### 2. Check the phone (read-only)

```bash
for p in ro.build.PDA ro.oem_unlock_supported sys.oem_unlock_allowed ro.boot.warranty_bit; do
  printf '%-24s %s\n' $p "$(adb shell getprop $p)"; done
```

`ro.build.PDA` must be the firmware you patched (`C9000ZHU1CSC1`). If `sys.oem_unlock_allowed` is already `1`,
skip to step 4. On Oreo there is **no OEM unlocking toggle** in Developer options on this phone, and Download mode
shows `CROM SERVICE: ON`: a custom boot image would be refused, so unlock first.

### 3. Unlock the bootloader (wipes the phone twice)

1. Flash Nougat (allowed: same bootloader revision 1). The script verifies every file, compares the phone's partition
   table with the firmware's, shows the command and asks before flashing. CSC = clean install, wipes on first boot:
   ```bash
   scripts/flash_full_firmware.sh SAMFW.COM_SM-C9000_TGY_C9000ZHU1BRC2_fac.zip --dry-run   # look first
   scripts/flash_full_firmware.sh SAMFW.COM_SM-C9000_TGY_C9000ZHU1BRC2_fac.zip
   ```
   (Odin equivalent: BL, AP, CP, CSC from the zip.)
2. On Nougat: Settings → About phone → Software info → tap Build number 7× → Developer options → **OEM unlocking ON**,
   USB debugging on, connect to Wi-Fi.
3. `adb install crom-service-1.0.8.apk`, open **CROM Service**, accept, unlock. It reports when done.

### 4. Flash Oreo with the patched boot

```bash
scripts/flash_full_firmware.sh SAMFW.COM_SM-C9000_TGY_C9000ZHU1CSC1_fac.zip --boot out/boot-v2.img
```

This installs stock Oreo with the patched boot in one go (wipes), so Android sets up its storage under the patched
image. On the tested phone it was done in two steps instead (stock Oreo, set-up, then BOOT only with
`scripts/flash_patched_boot.sh`, which flashes v2). Going from stock to a patched boot, the next boot showed
**"Verification failed ... reset your device"**: pressing **Reset** wipes, then it boots normally. Going from v1 to
v2 later did not show it. If you see that screen after the one-step flash, the same applies.

Odin users: flash stock BL/AP/CP/CSC, then `out/boot-v2.tar.md5` in the AP slot.

### 5. Verify

```bash
adb shell getprop ro.boot.warranty_bit          # 1 = the custom boot image is running
python3 -I scripts/wake_monitor.py              # lock/wake 20-30 times, ~10 s apart; Ctrl+C for the summary
```

Expected: every wake `OK`, no `PANEL_ALIVE=0` at all. If a wake ever comes up black, frozen or garbled, the panel
really failed to start that time: lock and wake again.

Then turn Always-On Display on and lock the phone a few times: AOD should show, and the phone must not restart
(`adb shell cat /proc/uptime` keeps growing). With v1 this crashed within seconds.

## Undo / recovery

- **Stock boot back:** `scripts/flash_patched_boot.sh --stock` (Odin: `out/boot-stock-restore.tar.md5` in AP).
  Probably asks for a Reset again.
- **v1 instead of v2** (if v2 ever brings the loop back on your phone): turn Always-On Display **off and keep it off**,
  then `scripts/flash_patched_boot.sh --v1` (Odin: `out/boot-esd-off.tar.md5`).
- **Anything worse:** Download mode (power off, then hold **Vol Down + Home + Power**, Vol Up to continue) and a full
  stock flash. Force reboot from anywhere: hold **Vol Down + Power** ~7 s.
- **A stock reflash or OTA brings the loop back.** Flash the patched boot again (step 4). On the tested phone, the CROM
  unlock survived a full stock reflash.
- Never flash firmware with a lower bootloader revision than the phone has (the digit after `ZHU` in the version).

## Files

| Path | What |
|---|---|
| `scripts/patch_esd.py` | `info` / `unpack` / `patch` / `verify` the boot image (stdlib only, never runs firmware code); `--prop` picks the property |
| `scripts/flash_full_firmware.sh` | Full firmware flash with Heimdall: md5 check, lz4, PIT comparison, `--boot`, `--home-csc`, `--dry-run` |
| `scripts/flash_patched_boot.sh` | Flash only BOOT: v2 by default, `--v1`, `--stock`; refuses if the phone runs other firmware (`EXPECT_PDA`) |
| `scripts/wake_monitor.py` | Live `adb logcat` wake classifier, or replay a saved log |
| `scripts/make_odin_tars.sh` | Deterministic Odin `.tar.md5` packages and `out/SHA256SUMS` |
| `scripts/zipmd5.py` | Verify every `.tar.md5` inside a firmware zip without extracting it |
| `scripts/unlz4.py` | Stdlib LZ4 decoder, if the `lz4` tool isn't available |
| `out/SHA256SUMS` | Hashes of the tested stock and patched images and Odin packages |
| `docs/diagnosis-and-original-plan.md` | The original diagnosis (logs, device tree, driver code) and the v1 plan |
| `docs/v2-aod-crash-and-fix.md` | Why v1 crashes on Always-On Display, and the v2 patch and test plan |

## A gentler alternative

The AOD crash is a missing NULL check in Samsung's code (`ss_dsi_panel_common.c`, `mdss_samsung_panel_low_power_config`).
Instead of changing the device tree, the kernel could ignore the interrupt for ~1 s after power-on, or re-read GPIO 133
after a short delay and only reset if it's still high. That keeps real ESD recovery, but means building the kernel from
[android_kernel_samsung_msm8976 (`Touchwiz-Oreo`)](https://github.com/Galaxy-C9-Pro/android_kernel_samsung_msm8976/tree/Touchwiz-Oreo).
Unlock procedure credit: [XDA guide by Lojcs](https://xdaforums.com/t/guide-how-to-root-samsung-galaxy-c9-pro-sm-c9000.4000641/).

## License

[MIT](LICENSE). Samsung firmware, CROM Service and the kernel source belong to their respective owners and are not included.
