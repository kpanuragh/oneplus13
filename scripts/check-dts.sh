#!/usr/bin/env bash
# Compile dts/sm8750-oneplus-dodge.dts against mainline headers, quickly.
#
# Uses a sparse, blob-less checkout of just the DT sources and bindings
# (~40 MB instead of a full kernel clone), so it is fast enough for CI and
# for edit-compile loops.
#
#   scripts/check-dts.sh            # compile against linux master
#   LINUX_REF=v7.3 scripts/check-dts.sh
#   LINUX_DIR=~/src/linux scripts/check-dts.sh   # reuse an existing tree
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LINUX_REF="${LINUX_REF:-master}"
LINUX_DIR="${LINUX_DIR:-$ROOT/out/cache/linux-dt}"
OUT="$ROOT/out/dtb-check"
QCOM="$LINUX_DIR/arch/arm64/boot/dts/qcom"

if [ ! -d "$QCOM" ]; then
    git clone -q --depth 1 --filter=blob:none --sparse --branch "$LINUX_REF" \
        https://github.com/torvalds/linux "$LINUX_DIR"
    git -C "$LINUX_DIR" sparse-checkout set --no-cone \
        /include/dt-bindings/ /include/uapi/linux/ /arch/arm64/boot/dts/qcom/ \
        /scripts/dtc/include-prefixes/
fi
echo "linux: $(git -C "$LINUX_DIR" log -1 --format='%h %s' 2>/dev/null || echo "$LINUX_DIR")"

mkdir -p "$OUT"
cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp \
    -I "$ROOT/dts" -I "$QCOM" -I "$LINUX_DIR/scripts/dtc/include-prefixes" -I "$LINUX_DIR/include" \
    "$ROOT/dts/sm8750-oneplus-dodge.dts" -o "$OUT/dodge.dts.pp"
# Same warning set as the kernel's default W=0 build.
dtc -@ -I dts -O dtb -i "$QCOM" -o "$OUT/sm8750-oneplus-dodge.dtb" \
    -Wno-unit_address_vs_reg -Wno-avoid_unnecessary_addr_size -Wno-unique_unit_address \
    -Wno-graph_child_address -Wno-simple_bus_reg \
    "$OUT/dodge.dts.pp"
python3 "$ROOT/tools/dt_survey.py" --grep simple-framebuffer "$OUT/sm8750-oneplus-dodge.dtb"
echo "ok: $OUT/sm8750-oneplus-dodge.dtb"
