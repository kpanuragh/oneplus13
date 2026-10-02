# Boot chain, and what "pure Linux" can mean on this phone

## The stock chain

```
 Boot ROM (PBL, in silicon)
   └─ XBL_SC / XBL         partitions: xbl, xbl_config      DDR training, PMIC init
        ├─ TZ (QTEE)        tz                               secure world, EL3/S-EL1
        ├─ Gunyah           hyp                              hypervisor, owns EL2
        ├─ AOP, CPUCP, SHRM aop, cpucp, shrm                 power/clock co-processors
        └─ UEFI (XBL core)  uefi / imagefv                   display init, splash
             └─ ABL         abl  (LinuxLoader.efi)           fastboot, AVB, picks DTB
                  └─ boot.img (+ vendor_boot, init_boot, dtbo)  → Android kernel
```

Everything up to and including ABL is **signed with OnePlus's key, and
secure boot is fused on retail units**. These stages cannot be replaced.
No "fully open firmware" exists for this SoC the way it does for coreboot
laptops.

Consequences for Linux:

* **Linux runs at EL1 under Gunyah.** There is no EL2 access, so no KVM.
  Mainline already copes with this on SM8550/SM8650/SM8750 devices.
* **Co-processor firmware is signed and verified by TZ.** ADSP, CDSP,
  modem, the GPU zap shader, IPA and Wi-Fi each load an OEM-signed blob.
  Linux can only pass the stock images through; it cannot build its own.
* **Bootloader unlocking is the only gate.** Once unlocked
  (`fastboot flashing unlock`, which wipes userdata), ABL boots unsigned
  `boot.img` payloads in "orange" verified-boot state.

## What this project replaces

| Layer | Stock | This project |
|---|---|---|
| PBL / XBL / TZ / Gunyah / AOP | signed | **kept** (cannot change) |
| UEFI + ABL | signed | **kept**; optionally chain-loads U-Boot (stage 3) |
| Kernel | Android GKI (android15-6.6) + vendor modules | **mainline Linux** |
| Device tree | vendor_boot DTB + dtbo overlays | **`dts/sm8750-oneplus-dodge.dts`** |
| Co-processor firmware | `/vendor/firmware_mnt`, `/vendor/firmware` | **same blobs**, squashed into `/lib/firmware/qcom/sm8750/oneplus/dodge/` |
| Userspace | Android | any distro (postmarketOS, Debian, Fedora, Arch ARM ...) |

"Pure Linux firmware" here means a phone whose only non-free parts are the
signed boot stages and the co-processor blobs the silicon requires. Every
mainline Snapdragon phone port ends up in the same place.

## Boot paths

### Stage 1: `fastboot boot` (nothing flashed)

ABL accepts a header-v2 `boot.img` with the kernel, ramdisk and DTB inline.
`scripts/make-bootimg.sh` builds one; `fastboot boot out/boot-mainline.img`
runs it from RAM. A reboot returns to Android, so this is the safe way to
iterate.

ABL details that matter:

* **DTB selection.** ABL matches `qcom,msm-id` and `qcom,board-id` against
  the hardware. The mainline DTS carries the downstream values
  (`<618 0x20000>`, `<0x40008 0>`). If ABL rejects the image or falls back
  to another DTB, read the real values from `/proc/device-tree` on stock
  (`scripts/dump-live-device.sh` saves them to `dt-ids.txt`).
* **dtbo overlays.** ABL tries to apply the `dtbo` partition on top of the
  chosen DTB. With a mainline DTB that fails, which is normally harmless:
  ABL logs it and boots the base DTB. If it turns out to be fatal, flash an
  empty dtbo to the *inactive* slot and boot that slot.
* **Splash.** ABL keeps the display pipeline running only if the DTB has a
  reserved-memory node named exactly `splash_region`. That is why the DTS
  uses a non-standard node name there.
* **cmdline.** ABL appends its own `androidboot.*` arguments. They are
  harmless to mainline.

### Stage 2: flash to a slot

Flash `boot-mainline.img` to the inactive A/B slot (`boot_b`, say), keep
Android on the other slot, and switch with `fastboot set_active`. The root
filesystem lives on userdata or a dedicated partition (see `docs/flashing.md`).

### Stage 3: U-Boot, then standard UEFI distro boot

Upstream U-Boot can run as an Android `boot.img` payload on Snapdragon
phones (`qcom_defconfig` + `qcom-phone.config`). It then exposes EFI, so
stock distro images boot through systemd-boot or GRUB, and kernel updates
no longer need repacking. As of September 2026 U-Boot has clock and pinctrl
drivers for SM8550, SM8650 and X1E80100 but **not SM8750**. Porting
`clock-sm8750.c` and `pinctrl-sm8750.c` from the SM8650 ones is a
self-contained milestone (see `docs/roadmap.md`).
