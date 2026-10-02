# Extracting and reverse-engineering the stock firmware

## 1. Get a full OTA

Use a **full** OxygenOS / ColorOS OTA zip for the OnePlus 13 (around 6–7 GB,
containing `payload.bin`). Incremental OTAs do not work: they hold binary
diffs against images you do not have. Full packages come from OnePlus's
update servers. Community mirrors and "OTA link" tools list the URLs per
region and build.

Keep a note of the build number. Firmware blobs are not interchangeable
across major Android versions, so record which build they came from.

## 2. Run the pipeline

```sh
scripts/extract-firmware.sh ~/Downloads/CPH2653_15.0.0.xxx.zip out/
```

What it does:

| Step | Tool | Output |
|---|---|---|
| List and extract partitions from `payload.bin` (reads it inside the zip, verifies SHA-256) | `tools/payload_extract.py` | `out/images/*.img` |
| Unpack `boot`, `init_boot`, `vendor_boot` (v4, with ramdisk table) and `dtbo` | `tools/bootimg.py` | `out/unpacked/*` |
| Unpack `modem` (FAT), `bluetooth` (FAT), `dsp` (ext4), `vendor` (erofs) | mtools / debugfs / fsck.erofs | `out/fs/*` |
| Copy every firmware-looking file | — | `out/firmware/raw/` |
| Squash `.mdt` + `.bNN` into single `.mbn` files | `tools/qcom_fw.py squash` | `out/firmware/qcom/sm8750/oneplus/dodge/` |
| Version strings, DT hardware survey per project ID | `tools/qcom_fw.py scan`, `tools/dt_survey.py` | `out/report/` |

Install `mtools`, `e2fsprogs` and `erofs-utils` for the filesystem steps. The
Python tools need only the standard library, plus `zstandard` if the payload
uses ZSTD operations.

## 3. Firmware that mainline needs

Names are those used by mainline drivers for SM8750-class devices.
**Verify each one** against `out/report/firmware-versions.txt`: OEMs
sometimes rename images.

| Mainline file (`qcom/sm8750/oneplus/dodge/`) | Stock source | Used by |
|---|---|---|
| `adsp.mbn`, `adsp_dtb.mbn` | `modem` partition `image/adsp*.mdt` | Audio, sensors, **pmic-glink (battery, USB-C role)** |
| `cdsp.mbn`, `cdsp_dtb.mbn` | `image/cdsp*.mdt` | Compute DSP (FastRPC) |
| `modem.mbn`, `modem_dtb.mbn` | `image/modem*.mdt` | Cellular (not yet working upstream on SM8750) |
| GPU zap shader (`gen80100_zap.mbn` or similar) | `vendor/firmware/` or `image/` | Adreno 830 secure mode switch |
| `ipa_fws.mbn` | `image/` | IPA (modem data path) |
| Adreno SQE / GMU (`gen80100_sqe.fw`, `gen80100_gmu.bin`) | `vendor/firmware/` | GPU (not OEM-signed; linux-firmware may ship them) |
| ath12k board data / `amss.bin` / `m3.bin` | `vendor/firmware/` (WLAN) | Wi-Fi |
| BT `.tlv` / `.bin` patches | `bluetooth` partition | Bluetooth |

Signed blobs (`.mbn`) must stay byte-identical. Only the container changes
(split → squashed).

## 4. Reverse-engineering workflow

The Android side already exposes most of the hardware description. The work
is mostly translation, not disassembly:

1. **Board description.** `tools/dt_survey.py --project 23821 out/unpacked/dtbo/*.dtbo`
   lists every enabled device in the OnePlus overlays, with GPIOs and
   supplies resolved to labels (`reset-gpio=<&tlmm 98 0>`). The vendor_boot
   DTB gives the SoC base. Compare it with the published source tree; the
   binary is what actually ships.
2. **Runtime truth.** On a rooted stock phone, `scripts/dump-live-device.sh`
   grabs `/sys/firmware/fdt` (the final DTB after ABL merged the overlays),
   plus regulator voltages, clock rates, GPIO and pinmux state, i2c/spi
   device lists and `dmesg`. Pin and voltage values from here beat anything
   inferred.
3. **Display panel.** The panel init sequence lives in the downstream DTS
   (`qcom,mdss-dsi-on-command`). Feed the panel node to
   [linux-mdss-dsi-panel-driver-generator](https://github.com/msm8916-mainline/linux-mdss-dsi-panel-driver-generator)
   to produce a `drivers/gpu/drm/panel/` driver with DSC parameters.
4. **Vendor kernel modules.** For devices without DT-described behaviour
   (touch firmware loading, the alert slider, VOOC), read the vendor modules
   in `vendor_dlkm` / `vendor_boot` ramdisks. Most are GPL and published in
   the OnePlusOSS kernel repos; only fall back to Ghidra/IDA on `.ko` files
   when the source is missing.
5. **Boot stages.** `strings` over `xbl`/`abl` (`out/report/strings-*.txt`)
   shows build versions and the fastboot `oem` commands ABL supports. ABL is
   a PE/UEFI app inside an FV. `uefi-firmware-parser` / UEFITool extract
   `LinuxLoader.efi` for analysis of DTB selection and cmdline handling.
   This is read-only research; the signed stages cannot be modified.
