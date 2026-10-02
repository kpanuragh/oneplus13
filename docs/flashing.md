# Unlocking, booting and recovering

> Unlocking the bootloader **wipes all user data**, may affect the warranty
> and trips some app attestation (banking apps, Play Integrity). Do the
> firmware extraction from an OTA zip first. It needs no unlocked device.

## Before touching the phone

1. Back up anything you care about.
2. Keep the **full OTA zip of the exact build that is installed**, and
   replace it after every update. Never restore from an older build:
   anti-rollback fuses make downgrades brick the phone
   ([anti-rollback.md](anti-rollback.md)). Its
   images (`boot`, `vendor_boot`, `dtbo`, `init_boot`, `vbmeta`) are your
   restore set: `scripts/extract-firmware.sh` puts them in `out/images/`.
3. Install current `platform-tools` (adb/fastboot).

## Unlock

1. Settings → About → tap *Build number* 7× → Developer options →
   enable **OEM unlocking** and **USB debugging**.
2. `adb reboot bootloader`
3. `fastboot flashing unlock`, then confirm with the volume keys. The phone
   wipes itself and reboots.

Some carrier-locked or Chinese-market units may restrict unlocking. Check
before buying a test device.

## Optional: root for the live dump

Patch `init_boot.img` from your build with Magisk (or use KernelSU), flash
it, then run `scripts/dump-live-device.sh`. Flash the stock `init_boot.img`
back afterwards if you want to.

## Stage 1: boot mainline without flashing

```sh
scripts/build-kernel.sh          # out/kernel/{Image.gz,sm8750-oneplus-dodge.dtb,modules}
scripts/build-initramfs.sh       # out/initramfs.cpio.gz (busybox + USB gadget shell)
scripts/make-bootimg.sh          # out/boot-mainline.img
adb reboot bootloader
fastboot boot out/boot-mainline.img
```

What success looks like, in order:

1. The splash stays on and kernel text scrolls (simplefb + fbcon).
2. On the host, a USB NCM interface appears and `telnet 172.16.42.1` gives a
   shell, or `/dev/ttyACM0` does.
3. `dmesg` shows UFS enumerating the partitions.

If the screen goes black and USB never shows up, hold **Power + Vol−**
for about 10 s to force a reboot back to Android. Next, try booting with the
pstore/ramoops region enabled and read `/sys/fs/pstore` from Android
afterwards, or use the UART test pads (uart7, 115200 8N1) if you have them.

## Stage 2: install alongside Android (A/B)

* Flash the image to the **inactive** slot only:
  `fastboot getvar current-slot`, then `fastboot flash boot_b out/boot-mainline.img`
  if the current slot is `a`.
* Root filesystem options, safest first:
  1. **Network root during bring-up.** Stay in the initramfs and NFS-mount or
     `switch_root` into a rootfs served over the USB NCM link. Nothing is
     written to the phone.
  2. **Give userdata to Linux.** `fastboot flash userdata rootfs.img` (an
     ext4 image; fastboot sparses it automatically) and boot with
     `CMDLINE="... dodge.root=PARTLABEL=userdata"`. Android on the other
     slot then has no data partition. `fastboot -w` reformats it for
     Android again.
  3. **Repartition UFS** (shrink userdata, add a `linux` partition). This is
     the only real dual-boot, and the riskiest option: save the GPT layout
     from `out/live/partitions.txt` first, and never touch any partition
     other than userdata.

  Android encrypts userdata (file-based encryption), so a loop image copied
  in from Android is not readable from Linux. That is why there is no
  "image file on /data" option.
* Boot Linux: `fastboot set_active b`. Back to Android: `fastboot set_active a`.
  **Check anti-rollback first.** After any OTA, the other slot carries the
  previous build's bootloaders. If that OTA raised the ARB fuse, switching
  slots hard-bricks the phone. Compare both slots with
  `scripts/dump-live-device.sh` and read [anti-rollback.md](anti-rollback.md).

## Recovery

| Situation | Fix |
|---|---|
| Mainline hangs | Force reboot (Power + Vol−, ~10 s), then hold Vol− while booting to reach fastboot |
| Slot b broken | `fastboot set_active a`, but only if slot a's boot-chain ARB ≥ slot b's ([anti-rollback.md](anti-rollback.md)) |
| Android won't boot after experiments | Re-flash `boot`, `init_boot`, `vendor_boot`, `dtbo`, `vbmeta` from `out/images/` to the affected slot. They must come from the **currently installed** build; never use an older OTA |
| No fastboot at all | Qualcomm EDL (9008) mode with OnePlus's MSM Download Tool / signed firehose. That needs an authorised account for this generation, so **do not end up here**: never flash `xbl*`, `abl`, `tz`, `hyp`, `aop`, `devcfg` or `uefi`. |

**Never run `fastboot flashing lock` with a non-stock image installed.**
ABL will refuse to boot it, and relocking with unsigned images can leave the
phone unrecoverable without EDL.
