#!/usr/bin/env bash
# Pack kernel + DTB + initramfs into a boot.img that the stock ABL accepts.
#
#   scripts/make-bootimg.sh [out/boot-mainline.img]
#   CMDLINE="... dodge.root=PARTLABEL=linux" scripts/make-bootimg.sh
#
# Test it without flashing anything:
#   fastboot boot out/boot-mainline.img
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="${1:-$ROOT/out/boot-mainline.img}"
KERNEL="${KERNEL:-$ROOT/out/kernel/Image.gz}"
DTB="${DTB:-$ROOT/out/kernel/sm8750-oneplus-dodge.dtb}"
RAMDISK="${RAMDISK:-$ROOT/out/initramfs.cpio.gz}"
# clk_ignore_unused / pd_ignore_unused keep the splash display alive until
# real display support exists.
CMDLINE="${CMDLINE:-console=tty0 earlycon clk_ignore_unused pd_ignore_unused rootwait loglevel=7}"

for f in "$KERNEL" "$DTB" "$RAMDISK"; do
    [ -f "$f" ] || { echo "missing $f (run build-kernel.sh / build-initramfs.sh first)" >&2; exit 1; }
done

python3 "$ROOT/tools/bootimg.py" pack -o "$OUTPUT" \
    --kernel "$KERNEL" --dtb "$DTB" --ramdisk "$RAMDISK" --cmdline "$CMDLINE"
python3 "$ROOT/tools/bootimg.py" info "$OUTPUT"
