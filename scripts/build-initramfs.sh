#!/usr/bin/env bash
# Build the bring-up initramfs (busybox + initramfs/init, plus modules and
# firmware when they are available).
#
#   scripts/build-initramfs.sh [output.cpio.gz]
#
# Inputs (all optional, auto-detected):
#   BUSYBOX=/path/to/static/aarch64/busybox  (default: fetched from Alpine)
#   out/kernel/modules/                      from scripts/build-kernel.sh
#   out/firmware/qcom/                       from scripts/extract-firmware.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="${1:-$ROOT/out/initramfs.cpio.gz}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ALPINE="${ALPINE_MIRROR:-https://dl-cdn.alpinelinux.org/alpine/latest-stable/main/aarch64}"
CACHE="$ROOT/out/cache"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }

fetch_busybox() {
    mkdir -p "$CACHE"
    if [ ! -f "$CACHE/busybox" ]; then
        log "fetching busybox-static (aarch64) from Alpine"
        curl -fsSL "$ALPINE/APKINDEX.tar.gz" -o "$CACHE/APKINDEX.tar.gz"
        ver="$(tar -xzOf "$CACHE/APKINDEX.tar.gz" APKINDEX |
               awk '/^P:busybox-static$/{f=1} f&&/^V:/{sub(/^V:/,""); print; exit}')"
        curl -fsSL "$ALPINE/busybox-static-$ver.apk" -o "$CACHE/busybox-static.apk"
        tar -xzf "$CACHE/busybox-static.apk" -C "$CACHE" bin/busybox.static 2>/dev/null
        mv "$CACHE/bin/busybox.static" "$CACHE/busybox"
        rmdir "$CACHE/bin" 2>/dev/null || true
    fi
    echo "$CACHE/busybox"
}

BB="${BUSYBOX:-$(fetch_busybox)}"
file "$BB" 2>/dev/null | grep -q 'aarch64\|ARM aarch64' || echo "warning: $BB may not be an aarch64 binary" >&2

log "assembling rootfs in $WORK"
mkdir -p "$WORK"/{bin,sbin,etc,proc,sys,dev,tmp,run,var,lib/firmware,newroot,usr/bin,usr/sbin}
install -m 0755 "$BB" "$WORK/bin/busybox"
install -m 0755 "$ROOT/initramfs/init" "$WORK/init"
echo 'root::0:0:root:/:/bin/sh' > "$WORK/etc/passwd"

if [ -d "$ROOT/out/kernel/modules/lib/modules" ]; then
    log "adding kernel modules"
    cp -a "$ROOT/out/kernel/modules/lib/modules" "$WORK/lib/"
fi
if [ -d "$ROOT/out/firmware/qcom" ]; then
    log "adding firmware"
    cp -a "$ROOT/out/firmware/qcom" "$WORK/lib/firmware/"
fi

log "packing $OUTPUT"
mkdir -p "$(dirname "$OUTPUT")"
(
    cd "$WORK"
    { find . -mindepth 1 | sed 's|^\./||'; } |
        cpio -o -H newc --owner=0:0 --quiet
) > "$WORK.cpio"
# Append /dev/console as a second, concatenated cpio archive. The kernel
# accepts concatenated archives, and this way no root is needed for mknod.
python3 - "$WORK.cpio" <<'PY'
import sys
def entry(name, mode, rdevmaj=0, rdevmin=0):
    name = name.encode() + b"\0"
    hdr = "070701" + "".join("%08X" % v for v in (
        0, mode, 0, 0, 1, 0, 0, 0, 0, rdevmaj, rdevmin, len(name), 0))
    blob = hdr.encode() + name
    return blob + b"\0" * (-len(blob) % 4)
with open(sys.argv[1], "ab") as f:
    f.write(entry("dev/console", 0o020600, 5, 1))
    f.write(entry("TRAILER!!!", 0))
PY
gzip -9n < "$WORK.cpio" > "$OUTPUT"
rm -f "$WORK.cpio"
log "done: $OUTPUT ($(stat -c %s "$OUTPUT") bytes)"
