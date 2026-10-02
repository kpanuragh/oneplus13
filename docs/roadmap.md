# Porting roadmap

Each milestone lists what "done" means and where the work happens.

## Stage 0: reverse engineering (no device needed) ✅ tooling ready

- [x] OTA payload extractor, boot / vendor_boot / dtbo unpacker
- [x] MDT → MBN squasher and firmware version scanner
- [x] Downstream DT → hardware inventory (`dt_survey.py`)
- [x] Hardware map from OnePlus's published DT (`docs/hardware.md`)
- [ ] Run the pipeline on a current full OTA and commit `out/report/*.md`
      summaries (not blobs) to `docs/reports/`
- [ ] Live dump from a rooted stock phone; confirm msm-id / board-id,
      regulator voltages, Vol+ GPIO, Wi-Fi chip (WCN7850 vs WCN786x)

## Stage 1: boot to a shell

- [x] Board DTS that compiles against mainline (`dts/`)
- [x] Kernel config fragment, debug initramfs, boot.img packer
- [ ] `fastboot boot` reaches the initramfs, framebuffer console visible
- [ ] USB gadget shell over NCM / ACM
- [ ] UFS enumerates; rootfs on userdata (`dodge.root=PARTLABEL=userdata`)
- [ ] Physical buttons work

## Stage 2: core platform

- [ ] ADSP + pmic-glink up: battery level, charging status, USB-C role switch
      (then drop `dr_mode = "peripheral"`)
- [ ] CDSP
- [ ] Regulator ranges narrowed from the live `regulator_summary`
- [ ] Thermal zones and CPU frequency scaling (check upstream SM8750 cpufreq)
- [ ] Suspend / resume

## Stage 3: display and input

- [ ] Panel driver for AA569 (1440×3168 DSC command mode). Generate it with
      linux-mdss-dsi-panel-driver-generator from the downstream panel DT,
      then wire up `&mdss_dsi0` with reset `tlmm 98` and TE `tlmm 86`
- [ ] Backlight through DCS brightness
- [ ] Touch: Synaptics S3910 TouchComm. Needs a new upstream input driver;
      the reference is Synaptics' GPL `tcm` driver in the OnePlusOSS
      kernel modules
- [ ] Alert slider (hall sensors → `gpio-keys` switch events)

## Stage 4: GPU, audio and connectivity

- [ ] Adreno 830. Blocked on upstream `gpu` / `gmu` nodes in `sm8750.dtsi`
      (track freedreno / linux-arm-msm)
- [ ] Wi-Fi (ath12k on PCIe0) and Bluetooth (UART + `qcom,wcn7850-bt` or
      successor)
- [ ] Speakers: AW882xx on `i2c_hub_4` + WSA macro / AudioReach topology;
      UCM config for alsa-ucm-conf
- [ ] USB-C audio / DisplayPort alt-mode
- [ ] NFC (NXP SN-series)

## Stage 5: modem, cameras, polish

- [ ] MPSS (currently `fail` upstream on the MTP), rmtfs, qrtr; ModemManager
- [ ] CAMSS for SM8750 and sensor drivers
- [ ] Haptics (`swr_haptics`), IR blaster, wireless charging RX (NU1669)

## Stage 6: standard boot

- [ ] U-Boot: port SM8750 clock + pinctrl drivers (base them on
      `clock-sm8650.c` / `pinctrl-sm8650.c`), run as a boot.img payload, then
      boot EFI distro images with systemd-boot
- [ ] postmarketOS device package (`device-oneplus-dodge`,
      `linux-postmarketos-qcom-sm8750`, `firmware-oneplus-dodge`)
- [ ] Upstream `sm8750-oneplus-dodge.dts` to linux-arm-msm once stage 2 is
      stable. Before sending, run `make CHECK_DTBS=y` and write the
      `oneplus,dodge` binding entry in `qcom.yaml`
