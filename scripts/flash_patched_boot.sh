#!/usr/bin/env bash
# Flash a patched boot image to the BOOT partition with Heimdall: v2 by default, v1 with --v1, stock with --stock.
# Build the images in out/ first (README). The phone must run the firmware the image was built from
# (EXPECT_PDA, default C9000ZHU1CSC1) and be bootloader-unlocked (see README).
#   scripts/flash_patched_boot.sh           # v2: ESD check kept, ERR_FG pin ignored (AOD safe)
#   scripts/flash_patched_boot.sh --v1      # v1: ESD check removed (Always-On Display must stay OFF)
#   scripts/flash_patched_boot.sh --stock   # rollback to stock boot
set -euo pipefail
expect=${EXPECT_PDA:-C9000ZHU1CSC1}
cd "$(dirname "$0")/.."

case "${1:-}" in
    "")      img=out/boot-v2.img ;;
    --v1)    img=out/boot-patched.img ;;
    --stock) img=out/boot-stock.img ;;
    *)       echo "usage: $0 [--v1|--stock]"; exit 1 ;;
esac

[ -f "$img" ] || { echo "$img not found - build it first (README)"; exit 1; }
if [ "$expect" = C9000ZHU1CSC1 ]; then (cd out && sha256sum -c --ignore-missing SHA256SUMS) | grep -F "$(basename "$img")"; fi

if adb get-state >/dev/null 2>&1; then
    pda=$(adb shell getprop ro.build.PDA </dev/null | tr -d '\r')
    echo "phone firmware: $pda"
    [ "$pda" = "$expect" ] || { echo "phone runs $pda, image is for $expect - stop (boot.img must match the firmware)"; exit 1; }
    echo "unlock allowed: $(adb shell getprop sys.oem_unlock_allowed </dev/null | tr -d '\r') (must be 1)"
    if [ "$img" = out/boot-patched.img ]; then
        echo "Always-On Display: $(adb shell settings get system aod_mode </dev/null | tr -d '\r') (v1 crashes the phone on AOD: must be 0)"
    fi
fi

read -rp "Flash $img to BOOT? Phone will reboot to download mode if on adb. [yes/NO] " a
[ "$a" = "yes" ] || exit 1

adb get-state >/dev/null 2>&1 && adb reboot download
for _ in $(seq 1 60); do heimdall detect >/dev/null 2>&1 && break; sleep 2; done
heimdall detect
heimdall flash --BOOT "$img"
echo "Done. If the phone shows 'Verification failed ... reset', press Reset (expected after changing BOOT)."
