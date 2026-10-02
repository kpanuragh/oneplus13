#!/usr/bin/env bash
# Capture runtime hardware state from a OnePlus 13 running stock firmware.
#
#   scripts/dump-live-device.sh [outdir]
#
# Needs adb with USB debugging enabled. Root (Magisk / KernelSU) unlocks
# most of the useful data: the final merged DTB that ABL passed to the kernel,
# debugfs regulator / clock / GPIO / pinctrl state, and the full dmesg.
# Without root you get partitions, properties, module list and firmware
# file names only.
#
# Nothing on the phone is modified; everything is read-only.
set -uo pipefail

OUT="${1:-out/live}"
mkdir -p "$OUT"
ADB="${ADB:-adb}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

$ADB get-state >/dev/null 2>&1 || { echo "no device: enable USB debugging and authorise this host" >&2; exit 1; }

if [ "$($ADB shell id -u 2>/dev/null | tr -d '\r')" = "0" ]; then
    SU=""                          # adbd already runs as root (adb root)
elif $ADB shell 'su -c id' 2>/dev/null | grep -q 'uid=0'; then
    SU="su -c"
else
    SU=""
    echo "note: no root, collecting unprivileged data only" >&2
fi

sh_() { $ADB shell "$*" 2>/dev/null | tr -d '\r'; }
rsh() { if [ -n "$SU" ]; then $ADB shell "$SU '$*'" 2>/dev/null | tr -d '\r'; else sh_ "$*"; fi; }
pull_root() {   # copy a root-only file out via /data/local/tmp
    local src="$1" dst="$2" tmp="/data/local/tmp/.dump.$$"
    rsh "cat $src > $tmp && chmod 644 $tmp" && $ADB pull "$tmp" "$dst" >/dev/null 2>&1
    rsh "rm -f $tmp"
}

log "identity"
sh_ getprop > "$OUT/getprop.txt"
for k in ro.product.device ro.product.model ro.boot.prjname ro.boot.hardware.sku \
         ro.build.display.id ro.boot.slot_suffix ro.boot.verifiedbootstate \
         ro.boot.flash.locked ro.soc.model; do
    printf '%-32s %s\n' "$k" "$(sh_ getprop "$k")"
done | tee "$OUT/identity.txt"

log "partitions"
sh_ 'ls -l /dev/block/by-name/' > "$OUT/partitions.txt"
rsh 'for p in /dev/block/by-name/*; do n=$(basename $p); s=$(blockdev --getsize64 $p 2>/dev/null); echo "$n $s"; done' \
    > "$OUT/partition-sizes.txt"
rsh 'cat /proc/partitions' > "$OUT/proc-partitions.txt"

log "boot-chain anti-rollback versions, both slots (needs root)"
# Read-only copies of the signed boot chain, so tools/qcom_fw.py can report
# the ARB version of each slot. Compare slots before ever using set_active.
if [ -n "$SU" ] || [ "$(sh_ id -u)" = "0" ]; then
    mkdir -p "$OUT/bootchain"
    for p in xbl xbl_config abl tz hyp aop devcfg uefi; do
        for slot in _a _b; do
            [ -n "$(sh_ "ls /dev/block/by-name/$p$slot 2>/dev/null")" ] || continue
            pull_root "/dev/block/by-name/$p$slot" "$OUT/bootchain/$p$slot.img"
        done
    done
    if ls "$OUT"/bootchain/*.img >/dev/null 2>&1; then
        python3 "$(dirname "$0")/../tools/qcom_fw.py" arb "$OUT"/bootchain/*.img \
            | tee "$OUT/anti-rollback.txt"
    fi
fi

log "kernel and modules"
sh_ 'uname -a' > "$OUT/uname.txt"
rsh 'cat /proc/cmdline' > "$OUT/cmdline.txt"
rsh 'cat /proc/bootconfig' > "$OUT/bootconfig.txt"
rsh 'cat /proc/modules' > "$OUT/modules.txt"
rsh 'dmesg' > "$OUT/dmesg.txt"
rsh 'cat /proc/iomem' > "$OUT/iomem.txt"
rsh 'cat /proc/interrupts' > "$OUT/interrupts.txt"
rsh 'cat /proc/cpuinfo' > "$OUT/cpuinfo.txt"

log "device tree as passed by ABL (needs root)"
pull_root /sys/firmware/fdt "$OUT/live.dtb" && echo "  saved live.dtb ($(stat -c %s "$OUT/live.dtb" 2>/dev/null) bytes)"
for f in qcom,msm-id qcom,board-id qcom,pmic-id oplus,project-id model; do
    rsh "xxd -p /proc/device-tree/$f 2>/dev/null || cat /proc/device-tree/$f" \
        | tr -d '\n' | sed "s/^/$f: /"; echo
done > "$OUT/dt-ids.txt"

log "buses and devices"
rsh 'for d in /sys/bus/i2c/devices/*; do echo "$(basename $d) $(cat $d/name 2>/dev/null) $(readlink $d/driver | xargs basename 2>/dev/null)"; done' > "$OUT/i2c-devices.txt"
rsh 'for d in /sys/bus/spi/devices/*; do echo "$(basename $d) $(cat $d/modalias 2>/dev/null) $(readlink $d/driver | xargs basename 2>/dev/null)"; done' > "$OUT/spi-devices.txt"
rsh 'for d in /sys/bus/platform/devices/*; do echo "$(basename $d) $(readlink $d/driver | xargs basename 2>/dev/null)"; done' > "$OUT/platform-devices.txt"
rsh 'lspci 2>/dev/null; for d in /sys/bus/pci/devices/*; do echo "$(basename $d) $(cat $d/vendor):$(cat $d/device)"; done' > "$OUT/pci-devices.txt"
rsh 'for i in /sys/class/input/input*; do echo "$(basename $i): $(cat $i/name) [$(cat $i/phys 2>/dev/null)]"; done' > "$OUT/input-devices.txt"
rsh 'cat /sys/class/drm/*/modes 2>/dev/null; ls /sys/class/drm' > "$OUT/drm.txt"
rsh 'for r in /sys/class/remoteproc/*; do echo "$(basename $r) $(cat $r/name) $(cat $r/firmware 2>/dev/null) $(cat $r/state)"; done' > "$OUT/remoteproc.txt"
rsh 'cat /sys/devices/soc0/* 2>/dev/null; ls /sys/devices/soc0' > "$OUT/soc0.txt"

log "debugfs state (needs root)"
rsh 'mount -t debugfs none /sys/kernel/debug 2>/dev/null; true'
rsh 'cat /sys/kernel/debug/regulator/regulator_summary' > "$OUT/regulator_summary.txt"
rsh 'cat /sys/kernel/debug/clk/clk_summary' > "$OUT/clk_summary.txt"
rsh 'cat /sys/kernel/debug/gpio' > "$OUT/gpio.txt"
rsh 'for p in /sys/kernel/debug/pinctrl/*/pinmux-pins; do echo "== $p"; cat $p; done' > "$OUT/pinmux.txt"
rsh 'cat /sys/kernel/debug/interconnect/interconnect_summary' > "$OUT/interconnect.txt"

log "firmware file names"
rsh 'ls -lR /vendor/firmware_mnt/image /vendor/firmware /vendor/firmware/* /odm/firmware /vendor/dsp /vendor/bt_firmware 2>/dev/null' \
    > "$OUT/firmware-files.txt"

log "display panel"
rsh 'cat /proc/cmdline | tr " " "\n" | grep -i -E "panel|dsi|mdss"' > "$OUT/panel.txt"
rsh 'cat /sys/kernel/debug/dri/0/state 2>/dev/null | head -100' >> "$OUT/panel.txt"

# Drop empty files so the output shows clearly what could not be read.
find "$OUT" -type f -empty -delete
log "done: $OUT"
[ -f "$OUT/live.dtb" ] && echo "  next: python3 tools/dt_survey.py --markdown $OUT/live.dtb > $OUT/live-dt.md"
