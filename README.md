# OnePlus 13 → mainline Linux

Reverse-engineering toolkit and board support for running **mainline Linux**
on the **OnePlus 13** (codename `dodge`, Snapdragon 8 Elite / SM8750) instead
of Android.

The repository has two halves:

1. **Reverse-engineering tools.** They pull firmware, device trees and boot
   images out of stock OTAs and a live phone, and turn them into a hardware
   description.
2. **Board support.** A mainline device tree, kernel config, debug initramfs
   and boot image packer to boot that description.

> **What "pure Linux" means here.** The SM8750's early boot stages (XBL,
> TrustZone, the Gunyah hypervisor, ABL) are signed by OnePlus and enforced
> by fused secure boot, so they stay. Everything after the bootloader can be
> free: mainline kernel, upstream device tree, any distro. The DSP, modem,
> GPU-zap and Wi-Fi firmware blobs are signed and run on co-processors;
> they are extracted from stock and loaded unchanged. See
> [docs/boot-chain.md](docs/boot-chain.md).

## Status

| Area | State |
|---|---|
| OTA / boot image / DTB / firmware tooling | ✅ working, unit-tested |
| Hardware map from OnePlus's published DT | ✅ [docs/hardware.md](docs/hardware.md) |
| `sm8750-oneplus-dodge.dts` | ✅ compiles against Linux 7.3-rc5; ⏳ not yet booted on hardware |
| Framebuffer console, UFS, USB gadget, buttons | ⏳ described in DT; awaiting a hardware test |
| ADSP / CDSP, battery, USB-C roles | ⏳ described in DT; needs extracted firmware |
| Panel, touch, GPU, audio, Wi-Fi, modem, cameras | ❌ see [docs/roadmap.md](docs/roadmap.md) |

## Layout

```
tools/        Python 3 stdlib-only tools
  payload_extract.py   OTA payload.bin (or the OTA zip directly) → partition images
  bootimg.py           boot v0–4 / vendor_boot v3–4 / dtbo unpack; header-v2 pack
  qcom_fw.py           Qualcomm MBN/MDT info, .mdt+.bNN → .mbn squash, version scan
  fdt.py               DTB/DTBO parser, decompiler, carver
  dt_survey.py         downstream DTB/overlays → device inventory with resolved GPIOs
scripts/
  extract-firmware.sh  OTA → images, unpacked boot images, squashed firmware, reports
  dump-live-device.sh  adb dump of a running stock phone (live DTB, regulators, GPIOs…)
  build-kernel.sh      mainline kernel + DTB (+ modules)
  build-initramfs.sh   busybox initramfs with USB NCM/ACM gadget shell
  make-bootimg.sh      kernel + DTB + initramfs → boot.img for `fastboot boot`
  check-dts.sh         fast DTS compile check against mainline headers
dts/          sm8750-oneplus-dodge.dts (+ regulators dtsi)
kernel/       dodge.config, a fragment on top of arm64 defconfig
initramfs/    init script for the debug initramfs
docs/         hardware map, boot chain, firmware RE, flashing/recovery, roadmap
tests/        unit tests with synthetic images
```

## Quick start

```sh
# 0. host tools (Debian/Ubuntu)
sudo apt install python3 device-tree-compiler cpp mtools e2fsprogs erofs-utils \
                 gcc-aarch64-linux-gnu make bc bison flex libssl-dev cpio adb fastboot

# 1. reverse-engineer a stock OTA (no phone needed)
scripts/extract-firmware.sh CPH2653_full_ota.zip out/
less out/report/dtbo-survey-23821.md

# 2. (optional, rooted stock phone) capture runtime truth
scripts/dump-live-device.sh out/live

# 3. build and boot mainline from RAM; nothing gets flashed
scripts/build-kernel.sh
scripts/build-initramfs.sh
scripts/make-bootimg.sh
adb reboot bootloader && fastboot boot out/boot-mainline.img
telnet 172.16.42.1        # once the USB NCM link comes up
```

Read [docs/flashing.md](docs/flashing.md) **before** unlocking or flashing
anything. It covers the A/B-slot approach that keeps Android bootable and
the partitions you must never write.

## Development

```sh
python3 -m unittest discover -s tests -v   # tool tests (synthetic images)
scripts/check-dts.sh                       # DTS compile check (sparse mainline checkout)
```

CI (`.github/workflows/ci.yml`) runs both, plus pyflakes and shellcheck.

## Sources and prior art

- OnePlus kernel / DT sources:
  [OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750)
  (`oneplus/sm8750_b_16.0.0_oneplus_13`)
- Mainline SM8750 support: `arch/arm64/boot/dts/qcom/sm8750{.dtsi,-mtp.dts,-qrd.dts}`
- [linux-mdss-dsi-panel-driver-generator](https://github.com/msm8916-mainline/linux-mdss-dsi-panel-driver-generator)
  for the panel driver
- postmarketOS SM8550/SM8650 ports, for structure and initramfs conventions

## Legal

The DTS files are BSD-3-Clause, matching the upstream Qualcomm files they
derive from. No license has been chosen yet for the tools and scripts; add a
`LICENSE` file before publishing. **Do not commit extracted
firmware or OTA images**: they are OnePlus/Qualcomm property, and `out/`
is git-ignored for that reason. Distribute scripts that extract the blobs,
never the blobs themselves.
