> **About this document.** It was written as a hand-off for an AI coding assistant (Claude Code) after the
> diagnosis was done, which is why it talks about "the user" and asks for confirmation before risky steps.
> It is kept for the diagnosis (sections 1-2) and the background on how the patch works.
> **Where reality differed from this plan:** on Oreo this phone has *no* OEM unlocking toggle, and the
> China/HK bootloader also needs Samsung's CROM Service unlock; the image held a single DTB rather than several;
> and changing BOOT triggered a one-time "Verification failed, reset" screen. The [README](../README.md) has the
> procedure that actually worked.

# Galaxy C9 Pro (SM-C9000): disable the panel ESD check by patching the stock boot image

This is a hand-off document. It was written on a Mac after diagnosing the phone over adb and reading the
kernel source. You (Claude Code on a Linux machine) are to continue from here. The user will give you the
stock Samsung firmware file for the phone. Everything you need is in this document, including two tested
helper scripts (Appendix A and B).

---

## 0. Rules for this task (read first)

- **This is the user's own phone and they want this done**, but several steps are irreversible or wipe data.
  **Ask the user in chat and wait for a clear yes before each of these:** enabling OEM unlocking / unlocking
  the bootloader (wipes the phone), flashing anything (TWRP, boot image, full firmware), rebooting into
  download mode. Read-only `adb shell getprop`/`logcat` checks and all work on the Linux machine are fine without asking.
- **Never modify the original firmware files.** Work on copies. Keep the original `boot.img` safe; it is the rollback.
- **Firmware archives are untrusted data.** Extract into their own empty directory, keep scripts in a different
  directory, never execute anything from inside the firmware, and run Python with `-I`.
- **Do not download firmware or tools from random sites on your own.** If something is missing, tell the user what and why.
- If any check in this document doesn't match what you find (wrong firmware version, property not found,
  verification fails), **stop and report** instead of improvising.

---

## 1. The problem and the diagnosis (already done)

**Phone:** Samsung Galaxy C9 Pro, China variant. The problem started after a screen-related hardware issue.

**Symptom:** sometimes, when the screen is turned on with the power button, it lags 1–2 s and then the display
loops: about 0.25 s on and 0.2 s off, over and over. The phone itself keeps working (touch works). It never
recovers by itself; the user has to press power to lock and wake again, sometimes several times. Once a wake
succeeds it stays fine until the next screen-off. Always-On Display works without problems. Gently pressing
certain parts of the screen and its ribbon while waking makes it happen less often.

**What the logs showed (logcat, stock ROM):**

```
20:45:14.714  hwc_setPowerMode: Setting mode 2 on display: 0      <- screen ON
20:45:14.976  getPanelResetStatus: got change event in fb0 with PANEL_ALIVE=0   <- ~260 ms later
20:45:14.993  hwc_setPowerMode: Setting mode 0 on display: 0      <- driver/HWC power the panel OFF
20:45:15.221  hwc_setPowerMode: Setting mode 2 on display: 0      <- and ON again
20:45:15.489  ... PANEL_ALIVE=0                                    <- repeats ~every 0.5 s
```

Three such episodes in one evening (12, 25 and 14 panel resets); 3 of the 4 wakes captured that day looped.
The phone was charging at the time and the battery was 26 °C, so heat isn't a factor.

**Kernel log** (`/proc/last_kmsg` from the previous boot, which booted into off-mode charging first) showed the
same thing on all 3 screen-ons there:

```
[MDSS] esd_irq_enable : ESD irq already enabled
[MDSS] esd_irq_handler : ++                      (irq/580-esd_rec)
mdss_dsi_reg_status_check: Read status register returned error
mdss_fb_report_panel_dead: Panel has gone bad, sending uevent - PANEL_ALIVE=0
```

**Conclusion:** the panel raises its **ERR_FG ("error flag") line** about 0.25–0.3 s after a cold power-on. The
Samsung display driver treats that as a dead panel and resets it; the reset is another cold power-on, so the
line fires again → endless loop. AOD never showed the error in the logs, even though AOD restarts the
panel about every 80 s; why isn't proven (AOD uses the panel's low-power mode and a different on-sequence).
The root cause is hardware (connector/flex/panel); the user has already reseated the connector and found
nothing visibly wrong. **This patch is a workaround that stops the driver from acting on that line.**

---

## 2. What the driver does (source references)

Kernel source (Samsung's stock Oreo kernel for this phone, community mirror):
<https://github.com/Galaxy-C9-Pro/android_kernel_samsung_msm8976>, branch **`Touchwiz-Oreo`**
(Linux 3.10.84). Samsung's own site (opensource.samsung.com) search returned nothing; this mirror contains the
exact messages seen in the logs.

**Board device tree** `arch/arm/boot/dts/samsung/msm8976/msm8976-sec-c9lte-chn-r08.dtsi` (other revisions r00–r07 are similar):

```
#include ".../samsung/S6E3FA3_AMS598KH01/dsi_panel_S6E3FA3_AMS598KH01_fhd_octa_cmd.dtsi"
&pmx_mdss_esd {
	qcom,pins = <&gp 133>, <&gp 115>; // GPIO_133 : FG-ERR  // GPIO_115 : DISP_DET
};
&mdss_dsi0 { qcom,dsi-pref-prim-pan = <&ss_dsi_panel_S6E3FA3_AMS598KH01_FHD>; ... };
```

**Panel device tree** `drivers/video/msm/mdss/samsung/S6E3FA3_AMS598KH01/dsi_panel_S6E3FA3_AMS598KH01_fhd_octa_cmd.dtsi`:

```
qcom,esd_irq_gpio = <&msm_gpio 133 0>;
qcom,esd-check-enabled;                                  <- THE property we remove
qcom,mdss-dsi-panel-status-check-mode="reg_read_irq";
qcom,mdss-dsi-panel-status-value = <1>;
qcom,mdss-dsi-panel-status-irq-trigger="rising";
```

The panel's power-on command list also sets `/* PCD Setting */ CC 4C` (panel crack detection) and
`/* ERR_FG Setting */ ED 44` (which faults drive the ERR_FG pin).

**Driver** `drivers/video/msm/mdss/samsung/ss_dsi_panel_common.c`:

- `mdss_samsung_panel_on_post()`: `if (pinfo->esd_check_enabled) { esd_irq_enable(true...); is_enabled_esd_recovery = true; }`
- `esd_irq_handler()`: returns immediately if `!vdd->esd_recovery.is_enabled_esd_recovery`; otherwise schedules the
  status-check work, which ends in `mdss_fb_report_panel_dead()` → `PANEL_ALIVE=0` uevent → HWC power-cycles the panel.
- `mdss_samsung_panel_parse_dt_esd()`: with `reg_read_irq` mode it sets `ctrl->check_read_status = mdss_dsi_esd_irq_status`,
  which **does not read the panel at all**. It just returns `!ctrl->status_value`. So "Read status register returned error"
  only reflects that the GPIO interrupt fired; the panel's actual state is never queried.
- `pinfo->esd_check_enabled` comes from the standard Qualcomm parse of the boolean `qcom,esd-check-enabled`.
  Without it, the interrupt is never armed for recovery and the Qualcomm `mdss_dsi_status` checker doesn't run.

**Therefore:** deleting `qcom,esd-check-enabled` from the panel node in the device tree disables the reset loop
without touching the kernel binary.

**Expected side effects:**
- If the panel really is in a bad state at some wake, the screen may stay black, frozen or garbled
  instead of looping. The fix for that is the same as today: lock and wake again.
- A genuine electrostatic glitch would no longer auto-recover, which is rare.
- Other features (AOD, brightness, touch) are unaffected.

---

## 3. Device facts

| Item | Value |
|---|---|
| Model | SM-C9000 (Galaxy C9 Pro, China) |
| Codename / product | `c9ltechn` / `c9ltezh` |
| SoC | Snapdragon 653 (msm8976 family; `ro.board.platform=msm8952`) |
| Firmware (PDA) on the phone | **C9000ZHU1CSC1** (Android 8.0.0, build R16NW) |
| Fingerprint | `samsung/c9ltezh/c9ltechn:8.0.0/R16NW/C9000ZHU1CSC1:user/release-keys` |
| Kernel | 3.10.84 |
| Panel driver | Samsung S6E3FA3 (AMS598KH01), 1080p command-mode AMOLED |
| ESD GPIO | GPIO 133 (FG-ERR), rising edge, IRQ shows as `esd_rec` |
| Bootloader state | **Unknown, check first (section 6.1)** |
| TWRP | Official build exists: <https://twrp.me/samsung/c9ltechn.html> (maintainer travis82). Odin install supported. |

---

## 4. Tools on the Linux machine

```bash
sudo apt install device-tree-compiler lz4 python3 adb heimdall-flash unzip tar coreutils
```

(`device-tree-compiler` provides `dtc`/`fdtget`/`fdtput`, used only for cross-checking; the patch itself is done by the
Python script in Appendix A, standard library only.) On other distros the package names may differ.

Create a layout like:

```
~/c9fix/
  scripts/     patch_esd.py, wake_monitor.py   (from the appendices)
  fw/          the firmware archive, extracted here and nowhere else
  work/        copies of boot.img, outputs
  out/         final flashable files
```

---

## 5. Build the patched boot image (all on the Linux machine, no phone changes)

### 5.1 Check the firmware matches the phone

The firmware must be **C9000ZHU1CSC1**, the same build the phone runs, so the patched boot image matches the rest
of the system. Check the archive and file names: the AP file should look like `AP_C9000ZHU1CSC1_*.tar.md5`.
If the phone is connected, confirm with:

```bash
adb shell getprop ro.build.PDA        # expect C9000ZHU1CSC1
adb shell getprop ro.bootloader       # expect C9000ZHU1CSC1
```

If they don't match, **stop and tell the user.** The options are getting the matching firmware, or flashing TWRP and
backing up the phone's own boot partition and patching that instead (section 6.3B).

### 5.2 Extract boot.img

```bash
cd ~/c9fix/fw
unzip -n <firmware>.zip                 # if it's a zip; yields AP_/BL_/CP_/CSC_/HOME_CSC_ *.tar.md5
tar -tvf AP_*.tar.md5 | grep -i boot    # .tar.md5 is a normal tar with an md5 appended; tar reads it fine
tar -xvf AP_*.tar.md5 'boot.img*'
[ -f boot.img.lz4 ] && lz4 -d boot.img.lz4 boot.img
cp boot.img ../work/boot-stock.img
sha256sum ../work/boot-stock.img | tee ../work/boot-stock.sha256
```

(Odin checks the `.tar.md5` checksum when flashing; for this workflow only boot.img is used, so no separate check is needed.)

### 5.3 Inspect

```bash
cd ~/c9fix/work
python3 -I ../scripts/patch_esd.py info boot-stock.img
```

What to expect:
- `page=2048` (or 4096), non-zero kernel and ramdisk sizes, a **dt** section, usually a `QCDT` pack. If `dt=0` the
  DTBs are appended to the kernel instead; the script handles both.
- `id sha1 recipe matches: qc` (or `aosp`). Either is fine.
- Trailing bytes starting with `SEANDROIDENFORCE` (Samsung's signature block).
- Several `DTBs found`, one per board revision. In most or all of them, `qcom,esd-check-enabled` should be listed in a node
  whose path contains `S6E3FA3_AMS598KH01` (e.g. `/soc/qcom,mdss_mdp/qcom,mdss_dsi_ss_dsi_panel_S6E3FA3_AMS598KH01_FHD`).
  It may also appear in a few other panel nodes (e.g. a "PBA booting" dummy panel). Disabling it there too is harmless.

**If the property is not found anywhere, stop and report.** It would mean this firmware's device tree differs from the source.

### 5.4 Cross-check with dtc (optional but recommended)

```bash
python3 -I ../scripts/patch_esd.py unpack boot-stock.img stock_unpacked
for f in stock_unpacked/dtb_*.dtb; do dtc -q -I dtb -O dts -o "${f%.dtb}.dts" "$f"; done
grep -n "esd-check-enabled\|esd_irq_gpio\|status-check-mode\|irq-trigger" stock_unpacked/*.dts
```

You should see the same properties as in section 2, with the GPIO phandle pointing at gpio 133.

### 5.5 Patch and verify

```bash
python3 -I ../scripts/patch_esd.py patch  boot-stock.img boot-patched.img
python3 -I ../scripts/patch_esd.py verify boot-stock.img boot-patched.img     # must print RESULT: PASS
```

How the patch works: each `qcom,esd-check-enabled` property, a 12-byte FDT_PROP header with an empty value, is overwritten
with three `FDT_NOP` tokens. NOP is a standard device-tree token that parsers skip. Nothing moves: kernel, ramdisk,
section offsets, DTB sizes and the QCDT table stay byte-identical. The header `id` (SHA1) is recomputed with the
same recipe the original used. The Samsung signature tail is replaced with the bare string `SEANDROIDENFORCE`, the
common practice for custom Samsung boot images; it avoids an extra boot warning. `--tail keep` or `--tail none` are available.

`verify` checks:
- kernel, ramdisk and second are identical, unless DTBs are appended to the kernel, in which case only the target property bytes changed;
- the header is unchanged except the `id`;
- no byte changed outside the target properties;
- the property is gone everywhere;
- all DTBs still parse.

Cross-check the result with dtc; the only difference should be the removed line in each DTB:

```bash
python3 -I ../scripts/patch_esd.py unpack boot-patched.img patched_unpacked
for f in patched_unpacked/dtb_*.dtb; do dtc -q -I dtb -O dts -o "${f%.dtb}.dts" "$f"; done
for f in stock_unpacked/dtb_*.dts; do diff "$f" "patched_unpacked/$(basename "$f")"; done
```

Expected diff output: only lines like `<  qcom,esd-check-enabled;`.

### 5.6 Make flashable packages

```bash
cd ~/c9fix/out
cp ../work/boot-patched.img boot.img && tar -H ustar -cf boot-esd-off.tar boot.img && rm boot.img
md5sum -t boot-esd-off.tar >> boot-esd-off.tar && mv boot-esd-off.tar boot-esd-off.tar.md5
cp ../work/boot-stock.img boot.img && tar -H ustar -cf boot-stock-restore.tar boot.img && rm boot.img
md5sum -t boot-stock-restore.tar >> boot-stock-restore.tar && mv boot-stock-restore.tar boot-stock-restore.tar.md5
cp ../work/boot-patched.img ../work/boot-stock.img .
sha256sum * > SHA256SUMS
```

- `*.tar.md5`: for **Odin** (Windows), in the **AP** slot.
- Raw `.img`: for **Heimdall** (Linux) or **TWRP** ("Install → Install Image → Boot").

---

## 6. Phone side (ask the user before every step that changes the phone)

### 6.1 Read-only checks (no confirmation needed)

```bash
adb devices -l
for p in ro.build.PDA ro.bootloader ro.revision ro.boot.revision ro.oem_unlock_supported sys.oem_unlock_allowed \
         ro.boot.flash.locked ro.boot.verifiedbootstate ro.boot.warranty_bit ro.boot.veritymode ro.frp.pst; do
  printf '%-28s %s\n' "$p" "$(adb shell getprop $p)"; done
```

Interpretation:
- `ro.oem_unlock_supported=1` means OEM unlock exists. **If it is 0 or missing, custom images will not boot. Stop and tell the user.**
- `ro.boot.warranty_bit=1` means Knox is already tripped (nothing more to lose there).
- `ro.revision` is the hardware revision; it tells you which DTB is used, but all of them are patched anyway.

Also have the user look at the **Download mode** screen (power off, then hold **Volume Down + Home + Power**, and
press Volume Up to continue at the warning). Note the lines: `FRP LOCK`, `OEM LOCK`, `KG STATE`/`RMM STATE` if present,
`WARRANTY VOID`. If it shows `RMM STATE: Prenormal` or `KG STATE: Prenormal`, Samsung will block custom binaries. The
usual workaround is keeping the phone online and waiting about 7 days (168 h) after OEM unlock with it switched on; tell
the user if you see this. A plain Volume Down to cancel and reboot exits Download mode without changing anything.

### 6.2 Before unlocking (user does this, you remind them)

- **Back up everything.** Enabling OEM unlocking / unlocking wipes the phone.
- Make sure the user knows their Google account password (FRP after the wipe).
- Make sure the full stock firmware (the archive they gave you) is kept. It's the unbrick path.
- Knox trips permanently once a custom image is flashed: Samsung Pay, Secure Folder and Samsung Pass stop working, even after going back to stock.

### 6.3 Unlock and flash (each step needs a yes from the user)

1. Settings → About phone → Software info → tap **Build number** 7× → Developer options → **OEM unlocking** ON.
   This phone is a 2016–2018 Samsung, where this toggle is normally all that's needed. If the phone wipes or asks
   for confirmation, let it.
2. Re-enable USB debugging after the wipe, if you still need adb.

**Option A (simplest): flash the patched boot image directly**

- Heimdall (Linux), phone in Download mode:

  ```bash
  heimdall detect
  heimdall print-pit --no-reboot | grep -B1 -A6 -i 'boot'   # confirm the partition name is BOOT and its file is boot.img
  heimdall flash --BOOT ~/c9fix/out/boot-patched.img
  ```

  (If Heimdall fails with a protocol/session error on this phone, a known issue on some Samsung bootloaders, use Odin
  on Windows with `boot-esd-off.tar.md5` in **AP** instead. Don't keep retrying.)
- The first boot may show a warning such as "Kernel is not seandroid enforcing" or a yellow/red notice. That's expected with
  an unlocked bootloader and a modified boot image.

**Option B: via TWRP, which gives an on-device backup of the real boot partition**

1. Flash official TWRP for **c9ltechn** (not c9lte) as RECOVERY, with Odin (AP slot) or Heimdall (`--RECOVERY twrp.img`).
   Ask the user to download it themselves from twrp.me, or ask before downloading it yourself.
2. **Immediately** boot into TWRP (Volume Up + Home + Power as the phone restarts); otherwise stock may overwrite TWRP.
3. In TWRP: Backup → select **Boot** only → save to SD card or internal storage, then copy it to the computer
   (`adb pull`). This is the phone's real boot partition; compare it with `boot-stock.img`. TWRP stores it as
   `boot.emmc.win`, a raw image. If the PDA matched, the sizes and hashes should match the firmware's boot.img,
   ignoring padding after the image's end.
4. Install → Install Image → `boot-patched.img` → **Boot** partition → reboot to system.
   Note: TWRP may offer to install its app or root; decline unless the user asks.

### 6.4 Verify on the phone

1. Boot fully and unlock with the screen on. Check the boot image actually changed: `adb shell getprop ro.boot.warranty_bit`
   should now be 1, and the boot warning seen earlier confirms the custom image loaded.
2. Run the wake test (Appendix B):

   ```bash
   python3 -I ~/c9fix/scripts/wake_monitor.py
   ```

   Have the user lock and wake 20–30 times, about 10 s apart. **Before the patch** the same script shows `LOOP (n panel resets)`
   on failing wakes (about 75% failed on the sample day). **After the patch** there should be **no `PANEL_ALIVE=0` at all**,
   so every wake reads `OK`. The real question is what the user *sees* on the wakes that would have looped: a normal
   screen (the alarm was false, so the patch fixes it), or black/frozen/garbled (the panel really failed to start).
3. After a later reboot, `adb shell cat /proc/last_kmsg | grep -iE "esd|PANEL_ALIVE|panel_dead"` covers the *previous*
   boot. It should show **no** `esd_irq_handler` and no `Panel has gone bad` lines.
   (`dmesg` for the current boot needs root, which this setup doesn't have.)

### 6.5 Rollback

- Flash `boot-stock.img` (Heimdall `--BOOT`) or `boot-stock-restore.tar.md5` (Odin AP), or restore the TWRP Boot backup.
- If the phone doesn't boot at all: Download mode → flash the **full stock firmware** with Odin (BL, AP, CP, CSC; use
  `HOME_CSC` to keep data). A bad boot image can't damage the bootloader, so Download mode stays reachable.
  Knox stays tripped; everything else returns to stock.

---

## 7. If the result isn't what we hoped

- **Wakes now show a black, frozen or garbled screen instead of looping:** the panel really fails to start
  sometimes. Locking and waking again works as before. The real fix is hardware: repairing or replacing the
  panel, flex cable or connector. The user can keep the patch (no worse than before) or roll back.
- **It still loops and `PANEL_ALIVE=0` still appears:** check the property is really gone from the image that was flashed
  (pull it back from TWRP and run `patch_esd.py info`). Check the flash went to BOOT and not RECOVERY, and that the
  firmware version matched.
- **More selective alternative (needs a kernel build):** instead of disabling the check, change `esd_irq_handler()` in
  `ss_dsi_panel_common.c` to ignore the interrupt for about 1 s after `mdss_samsung_panel_on_post()`, or to re-read GPIO 133
  after a short delay and act only if it's still high. That keeps protection against real ESD events. It means building
  the `Touchwiz-Oreo` branch: GCC 4.9 toolchain, the c9ltechn defconfig under `arch/arm64/configs/` or `arch/arm/configs/`, and
  `scripts/dtbTool` or the equivalent to rebuild the dt.img. Then repack with the stock ramdisk, keeping the stock
  cmdline, base and page size from `patch_esd.py info`. Only do this if the user asks; it's much more work than the
  device-tree patch.

---

## Appendix A: `patch_esd.py`

Save as `~/c9fix/scripts/patch_esd.py`. Standard library only. It never executes anything from the firmware.
Tested on synthetic boot images: a QCDT v2 pack with shared table entries, DTBs appended to the kernel, 2048-byte pages,
and the SEANDROIDENFORCE tail. `verify` caught a deliberately corrupted byte. **It has not yet been run on a real
SM-C9000 boot.img**, so read the `info` output carefully before trusting it.

```python
#!/usr/bin/env python3
"""
patch_esd.py - disable the panel ESD check in a Samsung/Qualcomm (msm8976, kernel 3.10)
Android boot image by turning the boolean property `qcom,esd-check-enabled` into FDT_NOP
tokens in every device tree blob found in the image.

Size-preserving: kernel, ramdisk and every section offset stay exactly the same; only the
12 bytes of each matching property header (+ its value, which is empty) become NOPs.

Usage:
  python3 -I patch_esd.py info    boot.img
  python3 -I patch_esd.py unpack  boot.img OUTDIR          # dump sections + each DTB
  python3 -I patch_esd.py patch   boot.img boot-patched.img [--prop NAME] [--tail seandroid|keep|none]
  python3 -I patch_esd.py verify  boot.img boot-patched.img   # prove only the intended bytes changed

Only standard library. Nothing from the firmware is executed.
"""
import argparse, hashlib, os, struct, sys

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9
DEFAULT_PROP = "qcom,esd-check-enabled"


def align(n, a):
    return (n + a - 1) // a * a


# ---------------------------------------------------------------- boot image (header v0, QC dt_size)
class BootImg:
    HDR_FMT = "<8s10I16s512s32s1024s"   # magic, k_size,k_addr, r_size,r_addr, s_size,s_addr, tags, page, dt_size, os_ver, name, cmdline, id, extra

    def __init__(self, data):
        if data[:8] != b"ANDROID!":
            sys.exit("not an Android boot image (no ANDROID! magic)")
        self.data = bytearray(data)
        f = struct.unpack_from(self.HDR_FMT, data, 0)
        (_, self.k_size, _, self.r_size, _, self.s_size, _, _, self.page,
         self.dt_size, self.os_ver, self.name, self.cmdline, self.id, _) = f
        if self.page not in (2048, 4096, 8192, 16384):
            sys.exit(f"unexpected page size {self.page}")
        p = self.page
        self.k_off = p
        self.r_off = self.k_off + align(self.k_size, p)
        self.s_off = self.r_off + align(self.r_size, p)
        self.dt_off = self.s_off + align(self.s_size, p)
        self.end = self.dt_off + align(self.dt_size, p)
        if self.end > len(data):
            sys.exit(f"header says image ends at {self.end}, file is only {len(data)} bytes")
        self.tail = bytes(data[self.end:])

    def section(self, name):
        off, size = {"kernel": (self.k_off, self.k_size), "ramdisk": (self.r_off, self.r_size),
                     "second": (self.s_off, self.s_size), "dt": (self.dt_off, self.dt_size)}[name]
        return off, size

    def sha1_id(self, include_dt):
        h = hashlib.sha1()
        for name in ("kernel", "ramdisk", "second") + (("dt",) if include_dt else ()):
            off, size = self.section(name)
            h.update(self.data[off:off + size])
            h.update(struct.pack("<I", size))
        return h.digest()

    def id_mode(self):
        """Which SHA1 recipe reproduces the stored id? 'qc' (with dt), 'aosp' (without), or None."""
        stored = self.id[:20]
        if self.sha1_id(True) == stored:
            return "qc"
        if self.sha1_id(False) == stored:
            return "aosp"
        return None

    def set_id(self, digest):
        id_off = struct.calcsize("<8s10I16s512s")
        self.data[id_off:id_off + 32] = digest.ljust(32, b"\0")


# ---------------------------------------------------------------- locating DTBs
def find_dtbs(img):
    """Return sorted unique (abs_offset, totalsize, where) of every valid FDT in dt section and kernel."""
    found = {}
    off, size = img.section("dt")
    if size:
        dt = bytes(img.data[off:off + size])
        if dt[:4] == b"QCDT":
            ver, n = struct.unpack_from("<II", dt, 4)
            words = {1: 5, 2: 6, 3: 10}.get(ver)
            if not words:
                print(f"  ! unknown QCDT version {ver}; falling back to magic scan")
            else:
                for i in range(n):
                    e = struct.unpack_from(f"<{words}I", dt, 12 + i * words * 4)
                    o, s = e[-2], e[-1]
                    found[off + o] = ("dt/QCDT", s)
        scan(img.data, off, size, found, "dt/scan")
    # DTBs appended to the kernel (zImage-dtb) when there is no dt section
    k_off, k_size = img.section("kernel")
    scan(img.data, k_off, k_size, found, "kernel-appended")
    out = []
    for o, (where, _) in sorted(found.items()):
        hdr = fdt_header(img.data, o)
        if hdr:
            out.append((o, hdr["totalsize"], where))
    return out


def scan(data, off, size, found, where):
    magic = struct.pack(">I", FDT_MAGIC)
    i = data.find(magic, off, off + size)
    while i != -1:
        h = fdt_header(data, i)
        if h and i + h["totalsize"] <= off + size:
            found.setdefault(i, (where, h["totalsize"]))
        i = data.find(magic, i + 4, off + size)


def fdt_header(data, o):
    if o + 40 > len(data):
        return None
    v = struct.unpack_from(">10I", data, o)
    keys = ("magic", "totalsize", "off_struct", "off_strings", "off_rsv", "version",
            "last_comp", "boot_cpuid", "size_strings", "size_struct")
    h = dict(zip(keys, v))
    if h["magic"] != FDT_MAGIC or not (16 <= h["version"] <= 17) or h["totalsize"] < 40:
        return None
    if h["off_struct"] >= h["totalsize"] or h["off_strings"] >= h["totalsize"]:
        return None
    return h


# ---------------------------------------------------------------- FDT walk / patch
def walk(data, base):
    """Yield (kind, path, prop_name, token_offset_abs, prop_len) for every node/prop."""
    h = fdt_header(data, base)
    s = base + h["off_struct"]
    strs = base + h["off_strings"]
    end = s + h["size_struct"] if h["version"] >= 17 else base + h["totalsize"]
    p, path = s, []
    while p < end:
        tok = struct.unpack_from(">I", data, p)[0]
        if tok == FDT_BEGIN_NODE:
            q = data.index(b"\0", p + 4)
            path.append(data[p + 4:q].decode("ascii", "replace"))
            p = align(q + 1 - base, 4) + base
        elif tok == FDT_END_NODE:
            path.pop()
            p += 4
        elif tok == FDT_PROP:
            ln, nameoff = struct.unpack_from(">II", data, p + 4)
            q = data.index(b"\0", strs + nameoff)
            name = data[strs + nameoff:q].decode("ascii", "replace")
            yield ("prop", "/" + "/".join(x for x in path if x), name, p, ln)
            p = p + 12 + align(ln, 4)
        elif tok == FDT_NOP:
            p += 4
        elif tok == FDT_END:
            return
        else:
            raise ValueError(f"bad FDT token {tok:#x} at {p - base:#x} in DTB @ {base:#x}")


def patch_dtb(data, base, prop):
    hits = []
    for kind, path, name, tokoff, ln in list(walk(data, base)):
        if name == prop:
            n = 12 + align(ln, 4)
            data[tokoff:tokoff + n] = struct.pack(">I", FDT_NOP) * (n // 4)
            hits.append((path, tokoff, n))
    return hits


def dtb_model(data, base):
    out = {}
    for kind, path, name, tokoff, ln in walk(data, base):
        if path == "/" and name in ("model",):
            v = bytes(data[tokoff + 12:tokoff + 12 + ln]).rstrip(b"\0").decode("ascii", "replace")
            out[name] = v
        if path == "/" and name in ("qcom,board-id", "qcom,msm-id"):
            out[name] = struct.unpack_from(f">{ln // 4}I", data, tokoff + 12)
    return out


# ---------------------------------------------------------------- commands
def cmd_info(a):
    img = BootImg(open(a.image, "rb").read())
    print(f"page={img.page} kernel={img.k_size} ramdisk={img.r_size} second={img.s_size} dt={img.dt_size}")
    name = img.name.rstrip(b"\0")
    cmdline = img.cmdline.rstrip(b"\0").decode(errors="replace")
    print(f"name={name!r}")
    print(f"cmdline={cmdline}")
    print(f"id sha1 recipe matches: {img.id_mode()}")
    print(f"trailing bytes after last section: {len(img.tail)} (starts {img.tail[:16]!r})")
    dtbs = find_dtbs(img)
    print(f"DTBs found: {len(dtbs)}")
    for o, sz, where in dtbs:
        m = dtb_model(img.data, o)
        props = [(p, ln) for k, p, n, t, ln in walk(img.data, o) if n == a.prop]
        print(f"  @{o:#x} size={sz} [{where}] {m.get('model', '?')} board-id={m.get('qcom,board-id')}"
              f" -> {a.prop}: {len(props)} at {[p for p, _ in props]}")


def cmd_unpack(a):
    img = BootImg(open(a.image, "rb").read())
    os.makedirs(a.outdir, exist_ok=True)
    for name in ("kernel", "ramdisk", "second", "dt"):
        off, size = img.section(name)
        if size:
            open(os.path.join(a.outdir, name), "wb").write(img.data[off:off + size])
    open(os.path.join(a.outdir, "tail"), "wb").write(img.tail)
    for i, (o, sz, where) in enumerate(find_dtbs(img)):
        open(os.path.join(a.outdir, f"dtb_{i:02d}_{o:#x}.dtb"), "wb").write(img.data[o:o + sz])
    print(f"written to {a.outdir}/ (decompile with: dtc -I dtb -O dts -o x.dts dtb_NN_*.dtb)")


def cmd_patch(a):
    raw = open(a.image, "rb").read()
    img = BootImg(raw)
    mode = img.id_mode()
    total = 0
    for o, sz, where in find_dtbs(img):
        hits = patch_dtb(img.data, o, a.prop)
        for path, tokoff, n in hits:
            print(f"  DTB @{o:#x} [{where}]: NOP'd {a.prop} in {path} ({n} bytes @ {tokoff:#x})")
        total += len(hits)
    if total == 0:
        sys.exit(f"ERROR: property {a.prop!r} not found in any DTB - nothing written")
    if mode:
        img.set_id(img.sha1_id(mode == "qc"))
        print(f"  header id recomputed ({mode} recipe)")
    else:
        print("  ! original header id matched no known recipe; left unchanged")
    body = bytes(img.data[:img.end])
    tail = {"keep": img.tail, "none": b"", "seandroid": b"SEANDROIDENFORCE"}[a.tail]
    open(a.out, "wb").write(body + tail)
    print(f"patched {total} occurrence(s) -> {a.out} ({len(body + tail)} bytes, tail={a.tail})")


def cmd_verify(a):
    A = BootImg(open(a.orig, "rb").read())
    B = BootImg(open(a.patched, "rb").read())
    ok = True
    # byte ranges of the target property in the ORIGINAL image: the only bytes allowed to change
    allowed = set()
    for o, sz, where in find_dtbs(A):
        for kind, path, name, tokoff, ln in walk(A.data, o):
            if name == a.prop:
                allowed.update(range(tokoff, tokoff + 12 + align(ln, 4)))
    for name in ("kernel", "ramdisk", "second"):
        if A.section(name) != B.section(name):
            print(f"FAIL {name}: layout differs"); ok = False; continue
        o, s = A.section(name)
        d = [i for i in range(o, o + s) if A.data[i] != B.data[i]] if A.data[o:o + s] != B.data[o:o + s] else []
        stray = [i for i in d if i not in allowed]
        sha = hashlib.sha256(A.data[o:o + s]).hexdigest()[:16]
        if not d:
            print(f"ok   {name}: sha256 {sha} identical")
        elif not stray:
            print(f"ok   {name}: {len(d)} bytes changed, all inside DTBs appended to it (target property only)")
        else:
            print(f"FAIL {name}: {len(stray)} bytes changed outside the target property"); ok = False
    hdr_len = struct.calcsize(BootImg.HDR_FMT)
    id_off = struct.calcsize("<8s10I16s512s")
    hdr_diff = [i for i in range(hdr_len) if A.data[i] != B.data[i] and not (id_off <= i < id_off + 32)]
    print(f"{'ok  ' if not hdr_diff else 'FAIL'} header (excluding id): {len(hdr_diff)} differing bytes")
    ok &= not hdr_diff
    lo, hi = A.k_off, A.end
    diff = [i for i in range(lo, hi) if A.data[i] != B.data[i]]
    stray = [i for i in diff if i not in allowed]
    print(f"{'ok  ' if not stray else 'FAIL'} section bytes changed: {len(diff)}, outside target property: {len(stray)}")
    ok &= not stray
    left = sum(1 for o, sz, w in find_dtbs(B) for k, p, n, t, l in walk(B.data, o) if n == a.prop)
    print(f"{'ok  ' if left == 0 else 'FAIL'} remaining {a.prop}: {left}")
    ok &= left == 0
    for o, sz, w in find_dtbs(B):
        list(walk(B.data, o))  # raises if structure broken
    print("ok   all patched DTBs still parse")
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info"); p.add_argument("image"); p.add_argument("--prop", default=DEFAULT_PROP)
    p.set_defaults(fn=cmd_info)
    p = sub.add_parser("unpack"); p.add_argument("image"); p.add_argument("outdir"); p.set_defaults(fn=cmd_unpack)
    p = sub.add_parser("patch"); p.add_argument("image"); p.add_argument("out")
    p.add_argument("--prop", default=DEFAULT_PROP)
    p.add_argument("--tail", choices=("seandroid", "keep", "none"), default="seandroid")
    p.set_defaults(fn=cmd_patch)
    p = sub.add_parser("verify"); p.add_argument("orig"); p.add_argument("patched")
    p.add_argument("--prop", default=DEFAULT_PROP); p.set_defaults(fn=cmd_verify)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
```

## Appendix B: `wake_monitor.py`

Save as `~/c9fix/scripts/wake_monitor.py`. It classifies each screen wake live from `adb logcat`. It was checked
against the logcat captured on the Mac: it reported 4 wakes, 3 looped (12, 25 and 14 resets), matching the manual analysis.

```python
#!/usr/bin/env python3
"""
wake_monitor.py - live classifier for screen wakes on the Galaxy C9 Pro (stock Oreo ROM).
Watches `adb logcat` for display power-on events and the panel-dead uevent and prints one line
per wake: OK, or LOOP with how many panel resets happened. Ctrl+C prints a summary.

  python3 -I wake_monitor.py            # then lock/unlock the phone ~20-30 times, ~10 s apart
  python3 -I wake_monitor.py saved.log  # replay a saved `adb logcat -v time` dump instead
"""
import re, subprocess, sys
from datetime import datetime

WINDOW = 3.0      # seconds after a wake in which a PANEL_ALIVE=0 counts as a failed wake
REARM = 1.5       # a power-on within this time after a panel reset is part of the loop, not a new wake

ts_re = re.compile(r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})")
mode_re = re.compile(r"hwc_setPowerMode: Setting mode (\d) on display: 0")


def ts(line):
    m = ts_re.match(line)
    return datetime.strptime("2000-" + m.group(1), "%Y-%m-%d %H:%M:%S.%f") if m else None


def main():
    if len(sys.argv) > 1:
        p = subprocess.Popen(["cat", sys.argv[1]], stdout=subprocess.PIPE, text=True, errors="replace")
    else:
        p = subprocess.Popen(["adb", "logcat", "-v", "time", "-T", "1"], stdout=subprocess.PIPE,
                             text=True, errors="replace")
    last_esd = None
    wake = None          # [start_time, esd_count]
    results = []

    def finish():
        nonlocal wake
        if wake:
            start, n = wake
            verdict = "OK" if n == 0 else f"LOOP ({n} panel resets)"
            results.append(n == 0)
            ok = sum(results)
            print(f"{start:%H:%M:%S}  wake #{len(results):<3} {verdict:<24} total ok {ok}/{len(results)}", flush=True)
            wake = None

    print("watching... lock/unlock the phone now (Ctrl+C to stop)", flush=True)
    try:
        for line in p.stdout:
            t = ts(line)
            if t is None:
                continue
            if wake and (t - wake[0]).total_seconds() > WINDOW and not (
                    last_esd and (t - last_esd).total_seconds() < REARM):
                finish()
            if "PANEL_ALIVE=0" in line and "getPanelResetStatus" in line:
                if last_esd is None or (t - last_esd).total_seconds() > 0.1:   # two HWC processes log each event
                    if wake:
                        wake[1] += 1
                last_esd = t
                continue
            m = mode_re.search(line)
            if m and m.group(1) == "2":
                if last_esd and (t - last_esd).total_seconds() < REARM:
                    continue                      # driver re-powering the panel inside a loop
                finish()
                wake = [t, 0]
    except KeyboardInterrupt:
        pass
    finally:
        finish()
        p.terminate()
        n = len(results)
        if n:
            print(f"\nsummary: {sum(results)}/{n} wakes OK, {n - sum(results)} looped "
                  f"({100 * (n - sum(results)) / n:.0f}% failure)")


if __name__ == "__main__":
    main()
```

## Appendix C: references

- Kernel source (stock Oreo, mirror): <https://github.com/Galaxy-C9-Pro/android_kernel_samsung_msm8976/tree/Touchwiz-Oreo>
- Other C9 Pro repos (device trees, LineageOS): <https://github.com/Galaxy-C9-Pro>
- TWRP for c9ltechn: <https://twrp.me/samsung/c9ltechn.html>, device tree <https://github.com/TeamWin/android_device_samsung_c9ltechn>
- Device-tree format (FDT_NOP token, header layout): Devicetree Specification, chapter 5 "Flattened Devicetree (DTB) Format".
