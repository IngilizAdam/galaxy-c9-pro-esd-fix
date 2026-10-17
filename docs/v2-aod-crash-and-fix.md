> **About this document.** Like [the v1 hand-off](diagnosis-and-original-plan.md), this was written for an AI
> coding assistant (Claude Code) after the diagnosis, which is why it talks about "the user" and asks before
> risky steps. It is kept for the crash analysis (sections 1-2) and why v2 works (section 3).
> **Outcome on the tested phone:** v2 built exactly as described (the property was also in an unused hx8399a
> panel node; both were NOP'd), flashing it over v1 did *not* trigger the "Verification failed, reset" screen,
> 18/18 quick wakes were OK, and AOD worked with no reboot. The [README](../README.md) has the current procedure;
> paths like `~/c9fix` below map to the repo's `work/` and `out/`.

# Galaxy C9 Pro (SM-C9000): follow-up. The v1 ESD patch crashes the kernel when Always-On Display is used. Fix: patch v2

This continues **C9PRO_ESD_PATCH_GUIDE.md** (v1, kept as [diagnosis-and-original-plan.md](diagnosis-and-original-plan.md)). You already ran v1 on this phone: you NOP'd
`qcom,esd-check-enabled` in the stock boot image and flashed it. It fixed the screen on/off loop, but it
introduced a kernel crash. This document explains the crash (already diagnosed over adb on the Mac) and
the replacement patch. **The rules in section 0 of v1 still apply:** ask the user before every flash, reboot to
Download mode, or settings change on the phone; never modify the originals; stop and report on any mismatch.

You should still have from v1: `boot-stock.img` (the firmware's original boot image, C9000ZHU1CSC1),
`boot-patched.img` (v1), `patch_esd.py` and `wake_monitor.py`. **Both scripts are unchanged and work for v2.**
`patch_esd.py` already has a `--prop` option. If you lost them, get them from Appendix A/B of the v1 guide.

---

## 0. Urgent: keep Always-On Display off while v1 is installed

With v1 installed, the phone crashes every time the panel enters or leaves Always-On Display (AOD). The
user's AOD runs on a **schedule from 07:00 to 00:00** (`aod_mode_start_time=420`, `aod_mode_end_time=0`), so
during those hours a reboot leads straight into another crash: a boot loop.

```bash
adb shell settings get system aod_mode      # 1 = on, 0 = off
```

If it's `1`, ask the user to turn it off (Settings → Lock screen → Always On Display), or ask permission to run
`adb shell settings put system aod_mode 0`. Keep it off until section 4 says to test it.

---

## 1. What happened (evidence)

The user reported the phone "turned off by itself at 30% battery". adb showed:

- **Android dropbox** (`adb shell dumpsys dropbox`): `SYSTEM_BOOT` every ~55 s from **07:00:54 to 07:22:10**,
  22 boots in a row, plus one more at 13:42. That's a boot loop, not a battery shutdown. The battery was fine
  (29–31%, about 3.9 V, 28 °C).
- `dumpsys dropbox --print SYSTEM_LAST_KMSG` for those boots (some entries are corrupt
  "invalid stored block lengths"; the 07:22:10 and 13:43:47 ones are readable), about 43 s after each boot:

```
[MDSS] mdss_samsung_panel_on_post : -: ndx=0
[MDSS] mdss_samsung_event_frame_update : DISPLAY_ON
[MDSS] mdss_samsung_panel_lpm_ctrl : [Panel LPM] En/Dis : Enable, LPM_MODE : HLPM, Hz : 30Hz, bl_level : 2NIT
Bad mode in Synchronous Abort handler detected, code 0x86000005
CPU: 3 PID: 585 Comm: surfaceflinger ... 3.10.84-14667562 #1
PC is at 0x0
LR is at mdss_samsung_panel_low_power_config+0xa0/0xc8
Kernel panic - not syncing: Fatal exception
```

- The kernel's own power-off reason for the boot before was "Triggered from PS_HOLD (MSM controlled shutdown)",
  a software reset, not an under-voltage. The off-mode charging boot (`Reboot cmd=lpm`) in `/proc/last_kmsg`
  is just the user plugging it in afterwards.
- Unrelated, already present before any patch: the off-mode `charger` process segfaults every 5 s
  (`charger: PC is at 0x40a260`). Ignore it.

Why it started at 07:00: v1 was flashed after midnight, when the AOD schedule was inactive, so the phone
worked. At 07:00 AOD switched on and the first panel transition into low-power mode crashed. Every
reboot then landed inside the schedule and crashed again.

---

## 2. Root cause (source)

Source: <https://github.com/Galaxy-C9-Pro/android_kernel_samsung_msm8976>, branch `Touchwiz-Oreo`,
`drivers/video/msm/mdss/`.

`samsung/ss_dsi_panel_common.c`, `mdss_samsung_panel_low_power_config()` (around line 5112), called from
`mdss_dsi.c` whenever the panel enters (`low_power_config(pdata, true)`) or leaves (`..., false`) the LP/AOD state:

```c
	mdss_samsung_panel_lpm_ctrl(pdata, enable);
	if (enable) vdd->esd_recovery.is_wakeup_source = true;
	else        vdd->esd_recovery.is_wakeup_source = false;

	vdd->esd_recovery.esd_irq_enable(true, true, (void *)vdd);   // <-- no NULL check
}
```

That function pointer is only assigned in `mdss_samsung_event_esd_recovery_init()`:
`esd->esd_irq_enable = esd_irq_enable;`. That init runs from the Samsung FB event callback, which is driven by the
ESD status machinery, and that only exists when `pinfo->esd_check_enabled` is true. v1 removed
`qcom,esd-check-enabled`, so the pointer stays NULL, and the first AOD transition jumps to address 0 →
panic. (Qualcomm's own call site in `mdss_dsi.c` around line 1347 *does* check `if (vdd->esd_recovery.esd_irq_enable)`;
Samsung's LPM code doesn't.)

**Correction to the v1 guide:** v1 said AOD would be unaffected by the patch. That was wrong; this crash path
was missed. Also, on the phone where v1 is installed, AOD must stay off.

---

## 3. The v2 patch: keep the ESD check, stop using the faulty error pin

Start again from **`boot-stock.img`, not from v1**. Leave `qcom,esd-check-enabled` alone, so the driver initializes
normally and the pointer is set. Instead, NOP the panel's **`qcom,mdss-dsi-panel-status-check-mode`** property
(value `"reg_read_irq"`).

What the driver does then (`mdss_dsi_panel.c`, `mdss_dsi_parse_esd_params()`, around line 1867–1950, and
`mdss_dsi.c` around line 3497–3512):

```c
	ctrl->status_mode = ESD_MAX;
	rc = of_property_read_string(np, "qcom,mdss-dsi-panel-status-check-mode", &string);
	if (!rc) { ... }            // property missing -> stays ESD_MAX, esd_check_enabled stays true
	...
	if (ctrl_pdata->status_mode == ESD_MAX) {
		pr_err("%s: Using default BTA for ESD check\n", __func__);
		ctrl_pdata->check_status = mdss_dsi_bta_status_check;
	}
```

- `esd_check_enabled` stays true, so the ESD init runs and `esd_irq_enable` is assigned. **The AOD crash can't happen.**
- `status_mode` isn't `ESD_REG_IRQ`, so `mdss_samsung_event_esd_recovery_init()` **never requests the GPIO 133
  (ERR_FG) interrupt.** The pin that was firing ~0.27 s after every cold power-on is no longer watched.
- The panel's health is instead checked by Qualcomm's standard **BTA (bus-turn-around) check**: the periodic
  `mdss_dsi_status` worker (default interval 5 s) asks the panel to respond on the DSI link. The panel is
  reset only if it really doesn't answer.

Expected harmless side effects: Samsung's `esd_irq_enable()` will still call `disable_irq_nosync`, `enable_irq`
and `enable_irq_wake` on the never-requested GPIO 133 IRQ during panel off/on and AOD transitions. At worst
that's kernel `WARN` traces ("Unbalanced enable for IRQ…"), not crashes. That's an inference from the 3.10 IRQ
core behaviour, not yet observed on the device; section 4 checks it.

What this does **not** guarantee: if the panel really fails to start on some wakes, BTA will catch it and
the reset loop could return on those wakes. That would be real hardware failure, not a false alarm, and the
answer would be the hardware fix or falling back to v1 with AOD off (section 5).

### 3.1 Build

```bash
cd ~/c9fix/work
python3 -I ../scripts/patch_esd.py info boot-stock.img --prop qcom,mdss-dsi-panel-status-check-mode
```

Check: the property is found in the **S6E3FA3_AMS598KH01** panel node of the DTBs. It may also be in other
panel nodes; that's fine, since only the attached panel is used. Also confirm `qcom,esd-check-enabled` is still
present in stock: run `info` without `--prop`. If the status-check-mode property isn't found, **stop and report**.

```bash
python3 -I ../scripts/patch_esd.py patch  boot-stock.img boot-v2.img --prop qcom,mdss-dsi-panel-status-check-mode
python3 -I ../scripts/patch_esd.py verify boot-stock.img boot-v2.img --prop qcom,mdss-dsi-panel-status-check-mode
# must print RESULT: PASS. Each NOP'd property is 28 bytes: 12-byte header + "reg_read_irq\0" padded to 16.
python3 -I ../scripts/patch_esd.py info boot-v2.img     # qcom,esd-check-enabled must STILL be listed (not NOP'd)
```

Cross-check with dtc as in v1 §5.4–5.5. The only diff vs stock should be the removed line
`qcom,mdss-dsi-panel-status-check-mode = "reg_read_irq";` in each DTB.

Make the packages like v1 §5.6:

```bash
cd ~/c9fix/out
cp ../work/boot-v2.img boot.img && tar -H ustar -cf boot-v2.tar boot.img && rm boot.img
md5sum -t boot-v2.tar >> boot-v2.tar && mv boot-v2.tar boot-v2.tar.md5
cp ../work/boot-v2.img . && sha256sum boot-v2.img boot-v2.tar.md5 >> SHA256SUMS
```

The bootloader is already unlocked and Knox already tripped from v1, so nothing new is lost.

### 3.2 Flash (ask the user first)

Confirm AOD is off (section 0). Then flash `boot-v2.img` the same way v1 was flashed: Heimdall `--BOOT`, Odin AP with
`boot-v2.tar.md5`, or TWRP Install Image → Boot.

---

## 4. Test plan

Before you start, record the baseline: `adb shell dumpsys dropbox | grep -c SYSTEM_BOOT`.

**Crash watcher.** Run this in a separate terminal during every step. Uptime going backwards means the phone rebooted:

```bash
prev=0; while sleep 5; do u=$(adb shell cat /proc/uptime 2>/dev/null | cut -d. -f1); \
  [ -n "$u" ] && { [ "$u" -lt "$prev" ] && echo "$(date +%T) REBOOT detected (uptime $prev -> $u)"; prev=$u; } \
  || echo "$(date +%T) device not reachable"; done
```

**Step 1: confirm the new config is live.** Ask the user before rebooting; this is a normal reboot.
`/proc/last_kmsg` holds the *previous* boot, so after the v2 boot, reboot once more and read it:

```bash
adb shell cat /proc/last_kmsg > lk.txt
grep -nE "Using default BTA|ESD_REG_IRQ|esd_irq_handler|PANEL_ALIVE|Panel has gone bad|Unbalanced|PC is at|Kernel panic" lk.txt
```

Expected: `Using default BTA for ESD check` present; **no** `status mode (ESD_REG_IRQ)`; no `esd_irq_handler`; no panic.
Possibly some `Unbalanced … IRQ` warnings, which are OK; report them.

**Step 2: wakes with AOD still off.** Run `python3 -I wake_monitor.py`. Have the user lock and wake 20–30 times, about 10 s apart.
Report `ok/total`. On v2, a `LOOP` would mean the BTA check judged the panel dead after that wake, which would be
real hardware failure. The pre-patch baseline was 3 of 4 wakes looping.

**Step 3: AOD.** Only if steps 1–2 were clean, and only after the user agrees: AOD on
(`settings put system aod_mode 1`, or the user turns it on). Keep the crash watcher running. Then:
- 20 cycles of: power button off → AOD visible (wait ~10 s) → power button on → wait ~5 s;
- one long AOD period of 10+ minutes, since the BTA check keeps running while the panel is in low-power mode;
- a reboot during the schedule hours (07:00–24:00) with the screen left off afterwards. That's exactly the boot-loop scenario.

Pass = no reboot detected, no new `SYSTEM_BOOT` in dropbox beyond the ones you caused, AOD shows normally, and
wake_monitor stays mostly or all `OK`.

**Step 4:** leave it in normal use for a day, then check `dumpsys dropbox` for unexpected `SYSTEM_BOOT` entries and
`dumpsys dropbox --print SYSTEM_LAST_KMSG | grep -E "PC is at|LR is at|Kernel panic"`.

---

## 5. Decision table / fallbacks

| Result | Do this |
|---|---|
| v2 passes everything | Done. Tell the user they can keep AOD and its schedule. |
| v2 is fine with AOD off, but AOD causes a reboot or AOD flicker/reset | Turn AOD off, report the `LR is at` line from the last_kmsg. Choose between v2 + AOD off, or v1 + AOD off. |
| v2 brings back the wake loop (`LOOP` in wake_monitor) | The panel really fails BTA after some wakes, so it's hardware. Offer: v1 + AOD permanently off (no loop, no AOD), or stock (AOD, loop), plus the hardware fix (display repair). |
| Phone boot-loops on v2 | Download mode (Vol Down + Home + Power) → flash `boot-stock.img` or v1 → report. |

**v1 + AOD off is a valid stable final state.** The crash path only runs on AOD (LP) transitions. If you keep v1,
make sure AOD is off *and* tell the user not to turn it back on, since it's a crash, not just a missing feature.

**Proper fix, only if the user asks.** It needs a kernel change, so it's much more work than the device-tree patches.
Add a NULL check before line 5150 in `ss_dsi_panel_common.c`:
`if (vdd->esd_recovery.esd_irq_enable) vdd->esd_recovery.esd_irq_enable(true, true, (void *)vdd);`.
Then use v1's device-tree change, or better, make `esd_irq_handler()` ignore interrupts in the first ~1 s after
`mdss_samsung_panel_on_post()`. Doing that means rebuilding the `Touchwiz-Oreo` kernel (GCC 4.9), whose source date
(Aug 2018) is older than the installed kernel (built 19 Mar 2019, `3.10.84-14667562`), so there's a mismatch risk. An
alternative is binary-patching the stock kernel's `blr` at `mdss_samsung_panel_low_power_config+0x9c` (the call
before the crash's LR at `+0xa0`) into a NOP (`d503201f`). That requires extracting symbols from the stock kernel
Image (e.g. vmlinux-to-elf, which you'd have to ask the user before downloading), verifying the instruction there
really is `blr xN`, then recompressing and repacking the kernel. Don't attempt either without the user's go-ahead.

---

## 6. Facts for reference

| Item | Value |
|---|---|
| Firmware | C9000ZHU1CSC1, Android 8.0.0 R16NW, kernel `3.10.84-14667562` (built 19 Mar 2019) |
| Panel driver | S6E3FA3 AMS598KH01, command mode, ESD pin GPIO 133 (FG-ERR), rising edge |
| AOD settings | `system aod_mode` (1/0), `aod_mode_start_time=420` (07:00), `aod_mode_end_time=0` (00:00), `aod_show_state=1` |
| Crash signature (v1 + AOD) | `PC is at 0x0`, `LR is at mdss_samsung_panel_low_power_config+0xa0/0xc8`, comm `surfaceflinger` |
| v1 property | `qcom,esd-check-enabled` (NOP'd in v1; **keep it** in v2) |
| v2 property | `qcom,mdss-dsi-panel-status-check-mode = "reg_read_irq"` (NOP'd in v2) |
| Useful adb (no root) | `/proc/last_kmsg` (previous boot), `dumpsys dropbox [--print TAG]`, `logcat`; `dmesg` and `/proc/reset_reason` need root |
