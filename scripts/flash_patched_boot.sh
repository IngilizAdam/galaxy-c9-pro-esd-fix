#!/usr/bin/env bash
# Flash the ESD-patched boot image (or the stock one with --stock) to the BOOT partition with Heimdall.
# Build out/boot-patched.img first (README). The phone must run the firmware the image was built from
# (EXPECT_PDA, default C9000ZHU1CSC1) and be bootloader-unlocked (see README).
#   scripts/flash_patched_boot.sh           # patched
#   scripts/flash_patched_boot.sh --stock   # rollback to stock boot
set -euo pipefail
expect=${EXPECT_PDA:-C9000ZHU1CSC1}
cd "$(dirname "$0")/.."

img=out/boot-patched.img
[ "${1:-}" = "--stock" ] && img=out/boot-stock.img

[ -f "$img" ] || { echo "$img not found - build it first (README)"; exit 1; }
if [ "$expect" = C9000ZHU1CSC1 ]; then (cd out && sha256sum -c --ignore-missing SHA256SUMS) | grep -F "$(basename "$img")"; fi

if adb get-state >/dev/null 2>&1; then
    pda=$(adb shell getprop ro.build.PDA | tr -d '\r')
    echo "phone firmware: $pda"
    [ "$pda" = "$expect" ] || { echo "phone runs $pda, image is for $expect - stop (boot.img must match the firmware)"; exit 1; }
    echo "unlock allowed: $(adb shell getprop sys.oem_unlock_allowed | tr -d '\r') (must be 1)"
fi

read -rp "Flash $img to BOOT? Phone will reboot to download mode if on adb. [yes/NO] " a
[ "$a" = "yes" ] || exit 1

adb get-state >/dev/null 2>&1 && adb reboot download
for _ in $(seq 1 60); do heimdall detect >/dev/null 2>&1 && break; sleep 2; done
heimdall detect
heimdall flash --BOOT "$img"
echo "Done. If the phone shows 'Verification failed ... reset', press Reset (expected after changing BOOT)."
