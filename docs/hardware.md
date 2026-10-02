# OnePlus 13 hardware map

Every row was read from OnePlus's published downstream sources
([`OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750`](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750),
branch `oneplus/sm8750_b_16.0.0_oneplus_13`) unless it says otherwise. Paths
are relative to that repository. "Mainline" is the state of Linux 7.3-rc5
(October 2026).

Rows marked **verify** are inferred and still need checking on real
hardware, using `scripts/dump-live-device.sh` on a rooted stock phone.

## Identity

| Item | Value | Source |
|---|---|---|
| Codename | `dodge` | `devicetree/oplus/dodge-*.dts` |
| OPLUS project IDs | `23821`, plus `23893` / `23894` / `23895` for the other regional SKUs (**verify** which ID maps to which market) | `oplus,project-id` in the dodge overlays |
| SoC | Snapdragon 8 Elite, SM8750, code name "sun" | `qcom/sun*.dtsi` |
| `qcom,msm-id` | `<618 0x20000>` (SM8750 v2) | `qcom/sun-v2.dtsi` |
| `qcom,board-id` | `<0x40008 0>` (MTP, subtype 4, "v8" power grid) | `qcom/sun-mtp-v8-overlay.dts` |
| Reference design | Qualcomm **MTP v8**: the OnePlus overlay `#include`s `../qcom/sun-mtp-v8-overlay.dts` | `oplus/dodge-23821-sun-overlay.dts` |

Because the phone is an MTP derivative, mainline `sm8750-mtp.dts` is the
starting point for regulators, UFS and USB.

## Peripherals

| Block | Part | Bus / address | GPIOs and supplies | Mainline status |
|---|---|---|---|---|
| Display panel | "AA569 P 3 A0019", 6.82" AMOLED, 1440×3168, 71×158 mm | DSI0, 4 lanes, command mode, DSC (slice 720×22), 30 bpp, 60/90/120 Hz | reset `tlmm 98` (`tlmm 97` for the secondary), TE `tlmm 86` (mdp_vsync), VDDIO 1.8 V, VCI 3.0 V, VDDR enable `pm8550ve_f gpio7` | No panel driver. The DSI/MDSS host is supported. Generate a driver from the downstream DT (see roadmap) |
| Alternate panels | AA545 (A0005), BF262 (A0021) | same | same | Probably prototype or second-source panels |
| Splash framebuffer | — | `0xfc800000`, size `0x2b00000` (`cont_splash_region`) | — | Used through `simple-framebuffer` |
| Touch | Synaptics **S3910** (TouchComm protocol, not RMI4) | `qupv3_se4_spi` = mainline `spi4` @ `0xa90000` | IRQ `tlmm 162`, reset `tlmm 161`, AVDD enable `pm8550vs_j gpio3`, VDD `L4B` | No mainline TouchComm driver |
| NFC | NXP SN-series (`qcom,sn-nci`) | `qupv3_se0_i2c` = `i2c0`, addr `0x28` | IRQ `tlmm 75`, VEN `tlmm 34`, CLKREQ `tlmm 35`, VBAT `tlmm 174` | **verify** whether `nxp-nci` can drive it |
| Speaker amps | 2× Awinic **AW882xx** smart PA | `qupv3_hub_i2c4` = `i2c_hub_4`, `0x34` and `0x35` | reset `tlmm 2`, `tlmm 164` | Mainline has `aw88261` / `aw88395` / `aw88399` drivers; **verify** the exact part |
| Audio DSP | LPASS (ADSP, AudioReach) | `swr0..3`, WSA2 macro used | — | ADSP remoteproc and q6apm are upstream |
| IR blaster | "kookong" IR (OPLUS driver) | `qupv3_se5_spi` = `spi5` | `tlmm 53`, supply `pm_humu_l9` | Could use `ir-spi` |
| Wireless charging | NuVolta **NU1669** RX | — | — | No driver |
| Wired charging | SuperVOOC / UFCS through ADSP (`oplus,adsp-voocphy`) | pmic-glink | — | Basic charging and battery reporting through `qcom_battmgr` (**verify**) |
| Battery | Silicon-carbon, "deep discharge" profile | — | — | Through pmic-glink |
| SIM / eSIM | eSIM detect `pm8550 gpio11`, eSIM enable `pmih010x gpio10`, SIM2 detect `pm8550ve_d gpio4` | — | — | Needs modem |
| RF cable detect | — | — | `tlmm 47` | — |
| Debug UART | QUP SE7 (`qupv3_se7_2uart`) = mainline `uart7` @ `0xa9c000` | test pads only | — | Supported |
| Storage | UFS 4.x | `ufs_mem_hc` | reset `tlmm 215`, VCC `L17B`, VCCQ `L1D` | Supported (inherited from MTP, **verify** rails) |
| USB-C | DWC3 + eUSB2 (PMIH0108 repeater) + QMP combo PHY, roles through pmic-glink | — | orientation `tlmm 61` | Supported (needs ADSP for role switching) |
| Keys | Power (`pon_pwrkey`), Vol− (`pon_resin`), Vol+ (`pm8550 gpio6`) | — | — | Supported. Vol+ pin taken from MTP, **verify** |
| Alert slider | three-position hall switch | — | **verify** | TODO |
| Cameras | 3 rear + front, on CCI0 and CCI1, OIS on tele | CAMSS | — | No SM8750 CAMSS upstream yet |
| GPU | Adreno 830 | — | — | No `gpu` node in mainline `sm8750.dtsi` yet |
| Wi-Fi / BT | Qualcomm FastConnect. The board uses the plain MTP v8 overlay rather than the `-kiwi` one; MTP v8 boards carry either a WCN7850 or a WCN786x (**verify** with `lspci` on stock) | PCIe0 | MTP: WLAN_EN `tlmm 16`, BT_EN `pm8550ve_f gpio3` | ath12k (WCN7850) is upstream |
| Modem | X80 (MPSS) | — | — | Upstream remoteproc node exists but is marked `fail` on the MTP |

## PMICs

Inherited from the MTP v8 layout (`sm8750-mtp.dts`): PM8550 (B), PM8550VE
(D, F, G, I), PM8550VS (J), PM8010 (M, N), PMK8550, PMIH0108, PMD8028,
PMR735D. Regulator rails live in `dts/sm8750-oneplus-dodge-regulators.dtsi`.
The downstream board files reference rails that match the MTP (for example
touch VDD on L4B, NFC and the panel on L12B/L13B-class rails).

## QUP serial engine mapping (downstream → mainline)

| Downstream label | Address | Mainline label |
|---|---|---|
| `qupv3_se0_i2c` | `0xa80000` | `i2c0` |
| `qupv3_se4_spi` | `0xa90000` | `spi4` |
| `qupv3_se5_spi` | `0xa94000` | `spi5` |
| `qupv3_se7_2uart` | `0xa9c000` | `uart7` |
| `qupv3_hub_i2c4` | `0x990000` | `i2c_hub_4` |
