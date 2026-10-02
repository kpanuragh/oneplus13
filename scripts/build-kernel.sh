#!/usr/bin/env bash
# Build a mainline kernel + OnePlus 13 DTB.
#
#   scripts/build-kernel.sh                 # clone/update linux into ./linux and build
#   LINUX_DIR=~/src/linux scripts/build-kernel.sh
#   LINUX_REF=v7.3 scripts/build-kernel.sh  # pin a tag / branch
#   DTB_ONLY=1 scripts/build-kernel.sh      # only compile the device tree
#
# The board DTS lives in dts/ in this repo. It is copied into the kernel tree
# (arch/arm64/boot/dts/qcom/) so it builds with the kernel's own dtc and
# dt-bindings headers.
#
# Output: out/kernel/{Image.gz,sm8750-oneplus-dodge.dtb,modules/}
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LINUX_DIR="${LINUX_DIR:-$ROOT/linux}"
LINUX_REPO="${LINUX_REPO:-https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git}"
LINUX_REF="${LINUX_REF:-master}"
OUT="${OUT:-$ROOT/out/kernel}"
BUILD="${BUILD:-$LINUX_DIR/build-dodge}"
JOBS="${JOBS:-$(nproc)}"
DTS_DST="$LINUX_DIR/arch/arm64/boot/dts/qcom"

export ARCH=arm64
if [ "$(uname -m)" != "aarch64" ]; then
    export CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
fi

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

if [ ! -d "$LINUX_DIR/.git" ]; then
    log "cloning $LINUX_REPO ($LINUX_REF)"
    git clone --depth 1 --branch "$LINUX_REF" "$LINUX_REPO" "$LINUX_DIR"
fi

log "installing board DTS into the kernel tree"
cp "$ROOT"/dts/*.dts "$ROOT"/dts/*.dtsi "$DTS_DST/"
grep -q 'sm8750-oneplus-dodge.dtb' "$DTS_DST/Makefile" ||
    sed -i '/sm8750-mtp.dtb/i dtb-$(CONFIG_ARCH_QCOM)\t+= sm8750-oneplus-dodge.dtb' "$DTS_DST/Makefile"

mkdir -p "$BUILD" "$OUT"
if [ ! -f "$BUILD/.config" ] || [ "$ROOT/kernel/dodge.config" -nt "$BUILD/.config" ]; then
    log "configuring (defconfig + kernel/dodge.config)"
    make -C "$LINUX_DIR" O="$BUILD" defconfig
    "$LINUX_DIR/scripts/kconfig/merge_config.sh" -m -O "$BUILD" \
        "$BUILD/.config" "$ROOT/kernel/dodge.config"
    make -C "$LINUX_DIR" O="$BUILD" olddefconfig
fi

log "building DTB"
make -C "$LINUX_DIR" O="$BUILD" -j"$JOBS" qcom/sm8750-oneplus-dodge.dtb
cp "$BUILD/arch/arm64/boot/dts/qcom/sm8750-oneplus-dodge.dtb" "$OUT/"

if [ "${CHECK_DTBS:-0}" = 1 ]; then
    log "dt-schema validation (needs: pip install dtschema)"
    make -C "$LINUX_DIR" O="$BUILD" CHECK_DTBS=y qcom/sm8750-oneplus-dodge.dtb
fi

[ "${DTB_ONLY:-0}" = 1 ] && { log "DTB: $OUT/sm8750-oneplus-dodge.dtb"; exit 0; }

log "building kernel and modules (-j$JOBS)"
make -C "$LINUX_DIR" O="$BUILD" -j"$JOBS" Image.gz modules
cp "$BUILD/arch/arm64/boot/Image.gz" "$OUT/"
rm -rf "$OUT/modules"
make -C "$LINUX_DIR" O="$BUILD" INSTALL_MOD_PATH="$OUT/modules" INSTALL_MOD_STRIP=1 modules_install >/dev/null
KREL="$(cat "$BUILD/include/config/kernel.release")"
echo "$KREL" > "$OUT/kernel.release"

log "done: $KREL"
ls -la "$OUT"
