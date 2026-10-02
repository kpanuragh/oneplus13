#!/usr/bin/env bash
# Pull everything needed for mainline bring-up out of a stock OnePlus 13 OTA.
#
#   scripts/extract-firmware.sh <ota.zip | payload.bin | dir-of-*.img> [outdir]
#
# Produces, under outdir (default: out/):
#   images/                 raw partition images from the payload
#   unpacked/boot, vendor_boot, dtbo, init_boot
#   fs/<partition>/         contents of modem / bluetooth / dsp / vendor ...
#   firmware/qcom/sm8750/oneplus/dodge/*.mbn   squashed remoteproc firmware
#   firmware/raw/           every other firmware-looking file, as shipped
#   report/                 DT surveys, firmware version list, partition list
#
# Optional host tools (used when present):
#   mtools (mcopy) for FAT partitions, e2fsprogs (debugfs) for ext4,
#   erofs-utils (fsck.erofs) for erofs, android-sdk-libsparse-utils (simg2img).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TOOLS="$HERE/../tools"
PY="${PYTHON:-python3}"

SRC="${1:?usage: $0 <ota.zip|payload.bin|images-dir> [outdir]}"
OUT="${2:-out}"
FW_DIR="$OUT/firmware/qcom/sm8750/oneplus/dodge"

# Partitions worth extracting. The big system/product images are skipped.
PARTS="boot,init_boot,vendor_boot,dtbo,vbmeta,modem,bluetooth,dsp,vendor,vendor_dlkm,xbl,xbl_config,abl,tz,hyp,aop,aop_config,devcfg,uefi,uefisecapp,imagefv,keymaster,qupfw,cpucp,cpucp_dtb,shrm,featenabler,soccp_dcd,soccp_debug,pdp,pdp_cdb,pvmfw"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

mkdir -p "$OUT"/{images,unpacked,fs,firmware/raw,report} "$FW_DIR"

# ---------------------------------------------------------------- 1. images
if [ -d "$SRC" ]; then
    log "using pre-extracted images from $SRC"
    cp -n "$SRC"/*.img "$OUT/images/" 2>/dev/null || true
else
    log "listing payload partitions"
    "$PY" "$TOOLS/payload_extract.py" --list "$SRC" | tee "$OUT/report/partitions.txt"
    log "extracting: $PARTS"
    "$PY" "$TOOLS/payload_extract.py" -o "$OUT/images" -p "$PARTS" "$SRC"
fi

# Un-sparse anything that came from a factory/fastboot package.
for img in "$OUT"/images/*.img; do
    [ -f "$img" ] || continue
    if [ "$(head -c4 "$img" | od -An -tx4 | tr -d ' ')" = "ed26ff3a" ]; then
        if have simg2img; then
            simg2img "$img" "$img.raw" && mv "$img.raw" "$img"
        else
            warn "$(basename "$img") is an Android sparse image and simg2img is missing"
        fi
    fi
done

# ----------------------------------------------------------- 2. boot images
for p in boot init_boot vendor_boot dtbo; do
    img="$OUT/images/$p.img"
    [ -f "$img" ] || continue
    log "unpacking $p"
    "$PY" "$TOOLS/bootimg.py" unpack "$img" "$OUT/unpacked/$p" | tee "$OUT/report/$p.txt"
done

# --------------------------------------------------------- 3. filesystems
fs_type() {
    local img="$1"
    if [ "$(dd if="$img" bs=1 skip=1080 count=2 2>/dev/null | od -An -tx2 | tr -d ' ')" = "ef53" ]; then
        echo ext4
    elif [ "$(dd if="$img" bs=1 skip=1024 count=4 2>/dev/null | od -An -tx4 | tr -d ' ')" = "e0f5e1e2" ]; then
        echo erofs
    elif [ "$(dd if="$img" bs=1 skip=510 count=2 2>/dev/null | od -An -tx2 | tr -d ' ')" = "aa55" ]; then
        echo fat
    else
        echo unknown
    fi
}

for p in modem bluetooth dsp vendor vendor_dlkm; do
    img="$OUT/images/$p.img"
    [ -f "$img" ] || continue
    dst="$OUT/fs/$p"
    mkdir -p "$dst"
    t="$(fs_type "$img")"
    log "extracting $p ($t)"
    case "$t" in
        fat)
            if have mcopy; then mcopy -s -n -p -i "$img" ::/ "$dst/"
            else warn "install mtools to read FAT partition $p"; fi ;;
        ext4)
            if have debugfs; then debugfs -R "rdump / $dst" "$img" >/dev/null 2>&1
            else warn "install e2fsprogs to read ext4 partition $p"; fi ;;
        erofs)
            if have fsck.erofs; then fsck.erofs --extract="$dst" --overwrite --no-preserve "$img" >/dev/null
            else warn "install erofs-utils to read erofs partition $p"; fi ;;
        *) warn "$p: unknown filesystem, skipped" ;;
    esac
done

# ------------------------------------------------------ 4. collect firmware
log "collecting firmware files"
find "$OUT/fs" -type f \( -iname '*.mdt' -o -iname '*.mbn' -o -iname '*.b[0-9][0-9]' \
        -o -iname '*.fw' -o -iname '*.bin' -o -iname '*.elf' -o -iname '*.jsn' \
        -o -iname '*.tlv' -o -iname '*.mbn.*' \) -print0 |
    while IFS= read -r -d '' f; do
        rel="${f#"$OUT"/fs/}"
        mkdir -p "$OUT/firmware/raw/$(dirname "$rel")"
        cp -p "$f" "$OUT/firmware/raw/$rel"
    done

# Squash split .mdt images into single .mbn files for mainline remoteproc.
find "$OUT/firmware/raw" -iname '*.mdt' -print0 |
    while IFS= read -r -d '' mdt; do
        name="$(basename "${mdt%.*}" | tr '[:upper:]' '[:lower:]')"
        if "$PY" "$TOOLS/qcom_fw.py" squash "$mdt" "$FW_DIR/$name.mbn" >/dev/null 2>&1; then
            echo "  $name.mbn  <-  ${mdt#"$OUT"/}"
        else
            warn "could not squash $mdt"
        fi
    done
# Single-file .mbn images (e.g. GPU zap shader, IPA) are copied as they are.
find "$OUT/firmware/raw" -iname '*.mbn' -print0 |
    while IFS= read -r -d '' mbn; do
        name="$(basename "$mbn" | tr '[:upper:]' '[:lower:]')"
        [ -e "$FW_DIR/$name" ] || cp -p "$mbn" "$FW_DIR/$name"
    done

# ---------------------------------------------------------------- 5. report
log "writing reports"
"$PY" "$TOOLS/qcom_fw.py" scan "$OUT/firmware/raw" > "$OUT/report/firmware-versions.txt" || true
"$PY" "$TOOLS/qcom_fw.py" scan "$FW_DIR" > "$OUT/report/firmware-squashed.txt" || true
for i in boot xbl abl tz hyp aop; do
    [ -f "$OUT/images/$i.img" ] || continue
    strings -n 8 "$OUT/images/$i.img" | grep -E 'IMAGE_VERSION_STRING|OEM_IMAGE|Build|BOOT\.' \
        | sort -u > "$OUT/report/strings-$i.txt" || true
done
if ls "$OUT"/unpacked/vendor_boot/dtb.*.dtb >/dev/null 2>&1; then
    "$PY" "$TOOLS/dt_survey.py" --markdown "$OUT"/unpacked/vendor_boot/dtb.*.dtb \
        > "$OUT/report/base-dtb-survey.md"
fi
if ls "$OUT"/unpacked/dtbo/*.dtbo >/dev/null 2>&1; then
    for proj in 23821 23893; do
        "$PY" "$TOOLS/dt_survey.py" --markdown --project "$proj" "$OUT"/unpacked/dtbo/*.dtbo \
            > "$OUT/report/dtbo-survey-$proj.md"
    done
fi

log "done"
echo "  squashed firmware : $FW_DIR"
echo "  reports           : $OUT/report"
