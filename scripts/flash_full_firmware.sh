#!/usr/bin/env bash
# Flash a full stock Samsung firmware zip (SamFW layout: BL/AP/CP/CSC/HOME_CSC *.tar.md5) with Heimdall.
# What Odin does with BL+AP+CP+CSC, without repartitioning. CSC = clean install (wipes data on first boot).
#   scripts/flash_full_firmware.sh SAMFW.COM_SM-C9000_TGY_C9000ZHU1CSC1_fac.zip [--home-csc] [--boot IMG]
#     --home-csc   use HOME_CSC instead of CSC (keeps data; only for the same Android version)
#     --boot IMG   flash IMG to BOOT instead of the firmware's boot.img (e.g. out/boot-patched.img)
#     --dry-run    verify, extract and map partitions, print the command, then stop (phone not touched)
# Needs ~4.5 GB free next to the zip while running; the extracted images are deleted afterwards.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
zip=$(realpath "$1"); shift
csc=CSC_; boot=""; dry=0
while [ $# -gt 0 ]; do
    case "$1" in
        --home-csc) csc=HOME_CSC_ ;;
        --boot) boot=$(realpath "$2"); shift ;;
        --dry-run) dry=1 ;;
        *) echo "unknown option $1"; exit 1 ;;
    esac; shift
done

work=$(dirname "$zip")/fw-flash.$$
mkdir "$work"
trap 'rm -rf "$work"' EXIT

echo "== verifying md5 of every tar inside $zip"
python3 -I "$here/zipmd5.py" "$zip" | tee "$work/verify.txt" | grep -E '^(OK|FAIL)'
grep -q '^FAIL' "$work/verify.txt" && { echo "md5 failure - stop"; exit 1; }

echo "== extracting BL AP CP $csc"
for p in BL_ AP_ CP_ "$csc"; do
    name=$(unzip -Z1 "$zip" | grep "^$p" | head -1)
    [ -n "$name" ] || { echo "no ${p}* in zip"; exit 1; }
    unzip -p "$zip" "$name" | tar -x -C "$work" --exclude='meta-data*'
done
for f in "$work"/*.lz4; do [ -e "$f" ] || continue; lz4 -dq --rm "$f" "${f%.lz4}"; done

# the partition table (PIT) ships in CSC (not HOME_CSC)
if ! ls "$work"/*.pit >/dev/null 2>&1; then
    unzip -p "$zip" "$(unzip -Z1 "$zip" | grep '^CSC_' | head -1)" | tar -x -C "$work" --wildcards '*.pit'
fi
pit=$(ls "$work"/*.pit | head -1)
heimdall print-pit --file "$pit" > "$work/fw-pit.txt"

# map each image file to the partition whose "Flash Filename" matches it
args=()
while IFS=$'\t' read -r part file; do
    [ -n "$file" ] || continue
    if [ "$part" = "BOOT" ] && [ -n "$boot" ]; then args+=(--BOOT "$boot"); continue; fi
    [ -f "$work/$file" ] && args+=("--$part" "$work/$file")
done < <(grep -E "Partition Name|Flash Filename" "$work/fw-pit.txt" | paste - - |
         sed -E 's/ *Partition Name: *//; s/\t *Flash Filename: */\t/')
unmapped=$(cd "$work" && ls | grep -vE '\.pit$|\.txt$' | while read -r f; do grep -q "Flash Filename: $f\$" fw-pit.txt || echo "$f"; done)
[ -z "$unmapped" ] || { echo "files with no partition in PIT: $unmapped - stop"; exit 1; }

n=$(( ${#args[@]} / 2 ))
echo "== $n partitions: heimdall flash --resume ${args[*]//$work\//}"
[ "$dry" = 1 ] && { echo "dry run - stopping before touching the phone"; exit 0; }

echo "== phone"
if adb get-state >/dev/null 2>&1; then adb reboot download; fi
for _ in $(seq 1 60); do heimdall detect >/dev/null 2>&1 && break; sleep 2; done
heimdall detect
heimdall print-pit --no-reboot > "$work/phone-pit.txt"
f() { grep -E "Partition Name|Flash Filename|Block Count|Block Size|Starting|Identifier" "$1"; }
diff <(f "$work/phone-pit.txt") <(f "$work/fw-pit.txt") >/dev/null || { echo "phone PIT differs from firmware PIT - stop"; exit 1; }
echo "PIT identical"

warn=""; [ "$csc" = CSC_ ] && warn=" CSC flash: THE PHONE WILL BE WIPED on first boot."
read -rp "Flash $n partitions now?$warn [yes/NO] " a
[ "$a" = "yes" ] || exit 1
heimdall flash --resume "${args[@]}"
