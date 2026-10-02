# Anti-rollback (ARB)

Two different mechanisms both get called "rollback protection". Only the
first one can brick a phone.

## 1. Qualcomm hardware anti-rollback: the dangerous one

Every signed boot-chain image (XBL, XBL config, TZ, Gunyah/hyp, AOP, devcfg,
UEFI, ABL, …) carries an **anti-rollback version** in its hash segment:

| MBN header | Where the version lives |
|---|---|
| v3 / v5 | Attestation certificate OU field `01 <16 hex> SW_ID`; the upper 32 bits are the ARB version |
| v7 | QTI and OEM metadata blocks after the header; the third word is `anti_rollback_version` |

The SoC holds a minimum version in one-time-programmable **QFPROM fuses**.
The boot ROM and XBL refuse any image whose version is below the fused
value. When a newer image with a higher version boots successfully, the
fuse gets raised, and fuses can never go back down.

Consequence: **once an update has raised the fuse, flashing the boot chain
of any older build hard-bricks the phone.** It falls through to EDL (9008)
mode, and recovering from EDL on this generation needs OnePlus's authorised
service tooling.

On the OnePlus 13, users reported in early 2026 that OxygenOS / ColorOS 16
builds raised the ARB level, and that downgrading to older firmware
afterwards bricked phones. Treat any downgrade as unsafe unless you have
checked the numbers yourself (below).

## 2. AVB rollback index: harmless when unlocked

`vbmeta` carries Android Verified Boot rollback indexes, stored in RPMB.
ABL enforces them **only while the bootloader is locked**. With an unlocked
bootloader, which this project requires anyway, they neither block a boot
nor get raised.

## What this means for mainline Linux

| Action | ARB risk |
|---|---|
| `fastboot boot` / flashing a mainline `boot.img` | **None.** boot.img is not a Qualcomm-signed MBN; ARB is never checked on it |
| Loading co-processor firmware (ADSP, CDSP, …) from Linux | **No brick risk.** If TrustZone rejects an image, remoteproc fails to start and logs an error. Use blobs from the build that is installed so this cannot happen |
| `fastboot set_active <other slot>` | **Possible brick.** After an OTA, the other slot still holds the *previous* build's boot chain. If that OTA raised ARB, the old slot cannot boot |
| Restoring stock images from `out/images/` | Safe only if they come from the build that is installed now. After every OTA, re-extract |
| Flashing an older full OTA, a fastboot ROM or an MSM-tool package | **Brick** if its boot-chain ARB is below the fused value |
| `fastboot flashing lock` with anything non-stock installed | Separate hazard (AVB): never do it |

## Checking ARB versions

```sh
# Versions inside an OTA (also written by extract-firmware.sh to out/report/anti-rollback.txt)
python3 tools/qcom_fw.py arb out/images/{xbl,xbl_config,abl,tz,hyp,aop,devcfg,uefi}.img

# Versions on the phone, both slots (rooted stock; writes out/live/anti-rollback.txt)
scripts/dump-live-device.sh out/live
```

Rules:

1. **Before `fastboot set_active`**, compare `out/live/anti-rollback.txt` for
   `_a` and `_b`. Switch only when the target slot's numbers are equal to
   or higher than the current slot's. If they differ, install the same
   full OTA once more so it lands on the other slot, then check again.
2. **Before flashing any stock package**, its ARB values must be ≥ the
   installed ones.
3. The fused value itself cannot be read from Linux without TrustZone
   help. The installed (booting) build's numbers are the safe lower bound:
   the fuse can't be higher than what currently boots.

`tools/qcom_fw.py arb` parses MBN v3, v5 and v7. If an image reports an
unsupported header version, do not assume it is safe; treat the
downgrade as unsafe.
