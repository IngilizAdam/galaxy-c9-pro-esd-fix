#!/usr/bin/env bash
# Rebuild the Odin packages (AP slot) from the raw images in out/. Deterministic, so the hashes in SHA256SUMS reproduce.
set -euo pipefail
cd "$(dirname "$0")/../out"
mk() {  # mk IMAGE TARNAME
    cp "$1" boot.img && tar -H ustar --owner=0 --group=0 --numeric-owner --mtime=@0 --mode=0644 -cf "$2.tar" boot.img && rm boot.img
    md5sum -t "$2.tar" >> "$2.tar" && mv "$2.tar" "$2.tar.md5"
}
mk boot-patched.img boot-esd-off
mk boot-stock.img boot-stock-restore
sha256sum boot-patched.img boot-stock.img boot-esd-off.tar.md5 boot-stock-restore.tar.md5 > SHA256SUMS
ls -la
