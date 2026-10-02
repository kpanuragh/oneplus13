#!/usr/bin/env python3
"""Unpack Android boot / vendor_boot / dtbo images, and pack a mainline boot.img.

    python3 tools/bootimg.py info   boot.img
    python3 tools/bootimg.py unpack vendor_boot.img out/vendor_boot/
    python3 tools/bootimg.py unpack dtbo.img        out/dtbo/
    python3 tools/bootimg.py pack -o boot-mainline.img --kernel Image.gz \\
            --dtb sm8750-oneplus-dodge.dtb --ramdisk initramfs.cpio.gz \\
            --cmdline "console=tty0 earlycon"

Supported formats:
  * boot.img header v0-v4 ("ANDROID!")
  * vendor_boot.img header v3/v4 ("VNDRBOOT"), including the v4
    vendor-ramdisk table and bootconfig section
  * dtbo.img / android DT table (magic 0xd7b7ab1e)

`pack` writes a header-v2 image with a separate DTB section. It is meant
for `fastboot boot` tests of a mainline kernel through the stock ABL.
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fdt as fdtlib  # noqa: E402

BOOT_MAGIC = b"ANDROID!"
VENDOR_MAGIC = b"VNDRBOOT"
DT_TABLE_MAGIC = 0xD7B7AB1E

RAMDISK_TYPES = {0: "none", 1: "platform", 2: "recovery", 3: "dlkm"}


def _pad(n, page):
    return (n + page - 1) // page * page


def _cstr(b):
    return b.split(b"\0", 1)[0].decode("utf-8", "replace")


def _write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _ramdisk_kind(data):
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    if data[:4] == b"\x02\x21\x4c\x18":
        return "lz4-legacy"
    if data[:4] == b"\x04\x22\x4d\x18":
        return "lz4"
    if data[:6] == b"070701":
        return "cpio"
    return "unknown"


# --------------------------------------------------------------------------
# boot.img
# --------------------------------------------------------------------------

def parse_boot(data):
    hv = struct.unpack_from("<I", data, 40)[0]
    h = {"header_version": hv}
    if hv >= 3:
        (h["kernel_size"], h["ramdisk_size"], h["os_version"], h["header_size"]) = \
            struct.unpack_from("<4I", data, 8)
        h["cmdline"] = _cstr(data[44:44 + 1536])
        h["signature_size"] = struct.unpack_from("<I", data, 1580)[0] if hv >= 4 else 0
        page = 4096
        off = page
        h["sections"] = {}
        for name, size in (("kernel", h["kernel_size"]), ("ramdisk", h["ramdisk_size"]),
                           ("boot_signature", h["signature_size"])):
            h["sections"][name] = (off, size)
            off += _pad(size, page)
        h["page_size"] = page
        return h

    (h["kernel_size"], h["kernel_addr"], h["ramdisk_size"], h["ramdisk_addr"],
     h["second_size"], h["second_addr"], h["tags_addr"], h["page_size"],
     _hv, h["os_version"]) = struct.unpack_from("<10I", data, 8)
    h["name"] = _cstr(data[48:64])
    h["cmdline"] = _cstr(data[64:576]) + _cstr(data[608:1632])
    page = h["page_size"]
    order = [("kernel", h["kernel_size"]), ("ramdisk", h["ramdisk_size"]),
             ("second", h["second_size"])]
    if hv >= 1:
        h["recovery_dtbo_size"], h["recovery_dtbo_offset"], h["header_size"] = \
            struct.unpack_from("<IQI", data, 1632)
        order.append(("recovery_dtbo", h["recovery_dtbo_size"]))
    if hv >= 2:
        h["dtb_size"], h["dtb_addr"] = struct.unpack_from("<IQ", data, 1648)
        order.append(("dtb", h["dtb_size"]))
    off = page
    h["sections"] = {}
    for name, size in order:
        h["sections"][name] = (off, size)
        off += _pad(size, page)
    return h


# --------------------------------------------------------------------------
# vendor_boot.img
# --------------------------------------------------------------------------

def parse_vendor_boot(data):
    h = {}
    (h["header_version"], h["page_size"], h["kernel_addr"], h["ramdisk_addr"],
     h["vendor_ramdisk_size"]) = struct.unpack_from("<5I", data, 8)
    h["cmdline"] = _cstr(data[28:28 + 2048])
    off = 28 + 2048
    h["tags_addr"] = struct.unpack_from("<I", data, off)[0]
    h["name"] = _cstr(data[off + 4:off + 20])
    h["header_size"], h["dtb_size"], h["dtb_addr"] = struct.unpack_from("<IIQ", data, off + 20)
    off += 36
    h["table_size"] = h["table_entries"] = h["table_entry_size"] = h["bootconfig_size"] = 0
    if h["header_version"] >= 4:
        (h["table_size"], h["table_entries"], h["table_entry_size"],
         h["bootconfig_size"]) = struct.unpack_from("<4I", data, off)
    page = h["page_size"]
    pos = _pad(h["header_size"], page)
    h["sections"] = {}
    for name, size in (("vendor_ramdisk", h["vendor_ramdisk_size"]), ("dtb", h["dtb_size"]),
                       ("ramdisk_table", h["table_size"]),
                       ("bootconfig", h["bootconfig_size"])):
        h["sections"][name] = (pos, size)
        pos += _pad(size, page)

    h["ramdisks"] = []
    if h["table_entries"]:
        toff = h["sections"]["ramdisk_table"][0]
        rd_base = h["sections"]["vendor_ramdisk"][0]
        for i in range(h["table_entries"]):
            e = toff + i * h["table_entry_size"]
            size, offset, rtype = struct.unpack_from("<3I", data, e)
            name = _cstr(data[e + 12:e + 44])
            board_id = struct.unpack_from("<16I", data, e + 44)
            h["ramdisks"].append({"name": name or "ramdisk%d" % i, "type": rtype,
                                  "offset": rd_base + offset, "size": size,
                                  "board_id": board_id})
    return h


# --------------------------------------------------------------------------
# dtbo.img
# --------------------------------------------------------------------------

def parse_dt_table(data):
    (magic, total, hdr_size, entry_size, count, entries_off, page, version) = \
        struct.unpack_from(">8I", data, 0)
    if magic != DT_TABLE_MAGIC:
        raise ValueError("not a DT table image")
    entries = []
    for i in range(count):
        e = entries_off + i * entry_size
        size, offset, ident, rev = struct.unpack_from(">4I", data, e)
        custom = struct.unpack_from(">4I", data, e + 16)
        entries.append({"size": size, "offset": offset, "id": ident, "rev": rev,
                        "custom": custom})
    return {"total_size": total, "page_size": page, "version": version,
            "entries": entries}


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _describe_dtbs(blob, label):
    for off, f in fdtlib.find_fdts(blob):
        r = f.root
        msm = r.cells("qcom,msm-id")
        board = r.cells("qcom,board-id")
        proj = r.cells("oplus,project-id")
        print("    %s+0x%06x %7d B overlay=%-3s model=%r msm-id=%s board-id=%s%s" % (
            label, off, f.totalsize, "yes" if f.is_overlay() else "no",
            r.str("model", ""), _hexlist(msm), _hexlist(board),
            " project-id=%s" % proj if proj else ""))


def _hexlist(c):
    return "-" if not c else "<" + " ".join("0x%x" % x for x in c) + ">"


def cmd_info(path, unpack_to=None):
    with open(path, "rb") as f:
        data = f.read()

    if data[:8] == BOOT_MAGIC:
        h = parse_boot(data)
        print("%s: boot image, header v%d, page %d" % (path, h["header_version"], h["page_size"]))
        print("  cmdline: %r" % h["cmdline"])
        for name, (off, size) in h["sections"].items():
            if not size:
                continue
            blob = data[off:off + size]
            extra = " (%s)" % _ramdisk_kind(blob) if name == "ramdisk" else ""
            print("  %-15s off=0x%08x size=%d%s" % (name, off, size, extra))
            if name == "dtb":
                _describe_dtbs(blob, "dtb")
            if unpack_to:
                _write(os.path.join(unpack_to, name), blob)
        return 0

    if data[:8] == VENDOR_MAGIC:
        h = parse_vendor_boot(data)
        print("%s: vendor_boot, header v%d, page %d" % (path, h["header_version"], h["page_size"]))
        print("  cmdline: %r" % h["cmdline"])
        for name, (off, size) in h["sections"].items():
            if size:
                print("  %-15s off=0x%08x size=%d" % (name, off, size))
        for rd in h["ramdisks"]:
            blob = data[rd["offset"]:rd["offset"] + rd["size"]]
            print("    ramdisk %-20s type=%-8s size=%d (%s)" % (
                rd["name"], RAMDISK_TYPES.get(rd["type"], rd["type"]), rd["size"],
                _ramdisk_kind(blob)))
            if unpack_to:
                _write(os.path.join(unpack_to, "ramdisk_%s" % rd["name"]), blob)
        off, size = h["sections"]["dtb"]
        dtb = data[off:off + size]
        _describe_dtbs(dtb, "dtb")
        if unpack_to:
            if not h["ramdisks"]:
                o, s = h["sections"]["vendor_ramdisk"]
                _write(os.path.join(unpack_to, "vendor_ramdisk"), data[o:o + s])
            _write(os.path.join(unpack_to, "dtb"), dtb)
            for i, (_, f) in enumerate(fdtlib.find_fdts(dtb)):
                _write(os.path.join(unpack_to, "dtb.%02d.dtb" % i), f.data)
            o, s = h["sections"]["bootconfig"]
            if s:
                _write(os.path.join(unpack_to, "bootconfig"), data[o:o + s])
        return 0

    if struct.unpack_from(">I", data, 0)[0] == DT_TABLE_MAGIC:
        t = parse_dt_table(data)
        print("%s: DT table v%d, %d entries" % (path, t["version"], len(t["entries"])))
        for i, e in enumerate(t["entries"]):
            blob = data[e["offset"]:e["offset"] + e["size"]]
            print("  [%02d] id=0x%x rev=0x%x size=%d" % (i, e["id"], e["rev"], e["size"]))
            _describe_dtbs(blob, "   ")
            if unpack_to:
                _write(os.path.join(unpack_to, "dtbo.%02d.dtbo" % i), blob)
        return 0

    print("%s: unrecognised image (magic %r)" % (path, data[:8]), file=sys.stderr)
    return 1


def cmd_pack(a):
    page = a.page_size

    def rd(p):
        if not p:
            return b""
        with open(p, "rb") as f:
            return f.read()

    kernel, ramdisk, dtb = rd(a.kernel), rd(a.ramdisk), rd(a.dtb)
    if a.cmdline.encode().__len__() > 512 + 1024 - 2:
        sys.exit("cmdline too long for a v2 header")
    cmd = a.cmdline.encode()
    cmd_main, cmd_extra = cmd[:511], cmd[511:]

    base = a.base
    hdr = struct.pack(
        "<8s10I16s512s32s1024sIQIIQ",
        BOOT_MAGIC,
        len(kernel), base + a.kernel_offset,
        len(ramdisk), base + a.ramdisk_offset,
        0, base + 0x00F00000,                 # second stage (unused)
        base + a.tags_offset,
        page, 2, 0,                           # page size, header v2, os_version
        b"", cmd_main, b"\0" * 32, cmd_extra,
        0, 0, 1660,                           # recovery dtbo size/offset, header size
        len(dtb), base + a.dtb_offset)
    out = bytearray(hdr.ljust(page, b"\0"))
    for blob in (kernel, ramdisk, dtb):
        out += blob + b"\0" * (_pad(len(blob), page) - len(blob))
    _write(a.output, bytes(out))
    print("wrote %s (%d bytes): kernel=%d ramdisk=%d dtb=%d" % (
        a.output, len(out), len(kernel), len(ramdisk), len(dtb)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info")
    p.add_argument("image")
    p = sub.add_parser("unpack")
    p.add_argument("image")
    p.add_argument("outdir")
    p = sub.add_parser("pack")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--kernel", required=True)
    p.add_argument("--ramdisk")
    p.add_argument("--dtb", required=True)
    p.add_argument("--cmdline", default="")
    p.add_argument("--page-size", type=int, default=4096)
    p.add_argument("--base", type=lambda s: int(s, 0), default=0x00000000)
    p.add_argument("--kernel-offset", type=lambda s: int(s, 0), default=0x00008000)
    p.add_argument("--ramdisk-offset", type=lambda s: int(s, 0), default=0x01000000)
    p.add_argument("--tags-offset", type=lambda s: int(s, 0), default=0x00000100)
    p.add_argument("--dtb-offset", type=lambda s: int(s, 0), default=0x01F00000)
    a = ap.parse_args()
    if a.cmd == "info":
        return cmd_info(a.image)
    if a.cmd == "unpack":
        return cmd_info(a.image, a.outdir)
    return cmd_pack(a)


if __name__ == "__main__":
    sys.exit(main())
