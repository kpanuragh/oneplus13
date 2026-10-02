#!/usr/bin/env python3
"""Self-tests for tools/ using synthetic images (no firmware needed).

    python3 -m unittest discover -s tests -v
"""

import hashlib
import lzma
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bootimg  # noqa: E402
import fdt  # noqa: E402
import payload_extract  # noqa: E402
import qcom_fw  # noqa: E402


# --------------------------------------------------------------------------
# helpers: tiny protobuf encoder + FDT builder
# --------------------------------------------------------------------------

def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def pb_int(num, val):
    return _varint(num << 3) + _varint(val)


def pb_bytes(num, val):
    return _varint(num << 3 | 2) + _varint(len(val)) + val


def build_fdt(props, children=()):
    """Build a DTB with root props and (name, props) children."""
    strings = bytearray()
    struct_ = bytearray()

    def stroff(name):
        key = name.encode() + b"\0"
        idx = strings.find(key)
        if idx < 0 or (idx and strings[idx - 1] != 0):
            idx = len(strings)
            strings.extend(key)
        return idx

    def begin(name):
        b = name.encode() + b"\0"
        struct_.extend(struct.pack(">I", 1) + b + b"\0" * (-len(b) % 4))

    def prop(name, val):
        struct_.extend(struct.pack(">III", 3, len(val), stroff(name)) + val +
                       b"\0" * (-len(val) % 4))

    begin("")
    for k, v in props:
        prop(k, v)
    for name, cprops in children:
        begin(name)
        for k, v in cprops:
            prop(k, v)
        struct_.extend(struct.pack(">I", 2))
    struct_.extend(struct.pack(">II", 2, 9))
    off_rsv = 40
    off_struct = off_rsv + 16
    off_strings = off_struct + len(struct_)
    total = off_strings + len(strings)
    hdr = struct.pack(">10I", 0xD00DFEED, total, off_struct, off_strings, off_rsv,
                      17, 16, 0, len(strings), len(struct_))
    return hdr + b"\0" * 16 + bytes(struct_) + bytes(strings)


def cells(*v):
    return struct.pack(">%dI" % len(v), *v)


# --------------------------------------------------------------------------

class FdtTest(unittest.TestCase):
    def test_parse_and_find(self):
        blob = build_fdt([("model", b"OnePlus 13\0"), ("qcom,msm-id", cells(618, 0x20000))],
                         [("chosen", [("bootargs", b"console=tty0\0")])])
        junk = b"\xaa" * 13 + blob + b"\x55" * 7 + blob
        found = list(fdt.find_fdts(junk))
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0][0], 13)
        f = found[0][1]
        self.assertEqual(f.root.str("model"), "OnePlus 13")
        self.assertEqual(f.root.cells("qcom,msm-id"), [618, 0x20000])
        self.assertEqual(f.root.find("/chosen").str("bootargs"), "console=tty0")
        self.assertIn('model = "OnePlus 13";', fdt.to_dts(f))


class PayloadTest(unittest.TestCase):
    def _make_payload(self, parts):
        bs = 4096
        data = bytearray()
        part_msgs = b""
        for name, content in parts:
            assert len(content) % bs == 0
            half = len(content) // 2 // bs * bs
            ops = b""
            # op 1: REPLACE_XZ for the first half
            comp = lzma.compress(content[:half])
            ops += pb_bytes(8, pb_int(1, 8) + pb_int(2, len(data)) + pb_int(3, len(comp)) +
                            pb_bytes(6, pb_int(1, 0) + pb_int(2, half // bs)) +
                            pb_bytes(8, hashlib.sha256(comp).digest()))
            data += comp
            # op 2: REPLACE for the rest (or ZERO if it is all zeros)
            rest = content[half:]
            if rest.strip(b"\0"):
                ops += pb_bytes(8, pb_int(1, 0) + pb_int(2, len(data)) + pb_int(3, len(rest)) +
                                pb_bytes(6, pb_int(1, half // bs) + pb_int(2, len(rest) // bs)))
                data += rest
            else:
                ops += pb_bytes(8, pb_int(1, 6) +
                                pb_bytes(6, pb_int(1, half // bs) + pb_int(2, len(rest) // bs)))
            info = pb_int(1, len(content)) + pb_bytes(2, hashlib.sha256(content).digest())
            part_msgs += pb_bytes(13, pb_bytes(1, name.encode()) + pb_bytes(7, info) + ops)
        manifest = pb_int(3, bs) + pb_int(12, 0) + part_msgs
        hdr = b"CrAU" + struct.pack(">QQI", 2, len(manifest), 0)
        return hdr + manifest + bytes(data)

    def test_extract_bin_and_zip(self):
        boot = os.urandom(4096 * 3) + b"\0" * 4096
        dtbo = b"\x11" * 4096 + b"\0" * 8192
        payload = self._make_payload([("boot", boot), ("dtbo", dtbo)])
        with tempfile.TemporaryDirectory() as td:
            pbin = os.path.join(td, "payload.bin")
            with open(pbin, "wb") as f:
                f.write(payload)
            ota = os.path.join(td, "ota.zip")
            with zipfile.ZipFile(ota, "w", zipfile.ZIP_STORED) as z:
                z.writestr("META-INF/com/android/metadata", "x")
                z.write(pbin, "payload.bin")
            for src in (pbin, ota):
                p = payload_extract.open_payload(src)
                self.assertEqual([x.name for x in p.partitions], ["boot", "dtbo"])
                for part, want in zip(p.partitions, (boot, dtbo)):
                    out = os.path.join(td, part.name + ".img")
                    payload_extract.extract_partition(p, part, out)
                    with open(out, "rb") as f:
                        self.assertEqual(f.read(), want)


class BootImgTest(unittest.TestCase):
    def test_pack_roundtrip(self):
        dtb = build_fdt([("model", b"OnePlus 13\0"), ("qcom,board-id", cells(0x40008, 0))])
        with tempfile.TemporaryDirectory() as td:
            k, r, d = (os.path.join(td, n) for n in ("Image.gz", "rd", "x.dtb"))
            for path, blob in ((k, b"\x1f\x8bK" * 1000), (r, b"070701" + b"r" * 777), (d, dtb)):
                with open(path, "wb") as f:
                    f.write(blob)
            out = os.path.join(td, "boot.img")
            cmd = [sys.executable, os.path.join(ROOT, "tools", "bootimg.py"), "pack", "-o", out,
                   "--kernel", k, "--ramdisk", r, "--dtb", d, "--cmdline", "console=tty0 " * 60]
            subprocess.run(cmd, check=True, capture_output=True)
            with open(out, "rb") as f:
                img = f.read()
            h = bootimg.parse_boot(img)
            self.assertEqual(h["header_version"], 2)
            self.assertEqual(h["cmdline"], ("console=tty0 " * 60))
            off, size = h["sections"]["dtb"]
            self.assertEqual(img[off:off + size], dtb)
            off, size = h["sections"]["ramdisk"]
            self.assertEqual(img[off:off + 6], b"070701")

    def test_vendor_boot_v4(self):
        page = 4096
        dtb = build_fdt([("model", b"sun\0")])
        rd1, rd2 = b"\x1f\x8b" + b"a" * 100, b"\x1f\x8b" + b"b" * 50
        ramdisk = rd1 + rd2
        entry = lambda size, off, typ, name: (struct.pack("<3I", size, off, typ) +
                                              name.ljust(32, b"\0") + b"\0" * 64)
        table = entry(len(rd1), 0, 1, b"") + entry(len(rd2), len(rd1), 3, b"dlkm")
        hdr = (b"VNDRBOOT" + struct.pack("<5I", 4, page, 0, 0, len(ramdisk)) +
               b"androidboot.hardware=qcom".ljust(2048, b"\0") + struct.pack("<I", 0) +
               b"".ljust(16, b"\0") + struct.pack("<IIQ", 2128, len(dtb), 0) +
               struct.pack("<4I", len(table), 2, 108, 0))
        pad = lambda b: b + b"\0" * (-len(b) % page)
        img = pad(hdr) + pad(ramdisk) + pad(dtb) + pad(table)
        h = bootimg.parse_vendor_boot(img)
        self.assertEqual(h["cmdline"], "androidboot.hardware=qcom")
        self.assertEqual([r["name"] for r in h["ramdisks"]], ["ramdisk0", "dlkm"])
        r = h["ramdisks"][1]
        self.assertEqual(img[r["offset"]:r["offset"] + r["size"]], rd2)
        off, size = h["sections"]["dtb"]
        self.assertEqual(img[off:off + size], dtb)


class QcomFwTest(unittest.TestCase):
    def test_squash(self):
        # ELF32 with: phdr segment (in mdt), hash segment (b01), one load segment (b02)
        phnum = 3
        ehdr_size, ph_size = 52, 32
        hdr_len = ehdr_size + phnum * ph_size
        hash_seg = b"QC_IMAGE_VERSION_STRING=ADSP.TEST.1.0\0" + b"\x30\x82" + b"h" * 60
        load_seg = os.urandom(300)
        offs = [0, 0x1000, 0x2000]
        sizes = [hdr_len, len(hash_seg), len(load_seg)]
        flags = [7 << 24, 2 << 24, 5]
        ehdr = (b"\x7fELF\x01\x01\x01" + b"\0" * 9 +
                struct.pack("<HHIIIIIHHHHHH", 2, 164, 1, 0x1000, ehdr_size, 0, 0,
                            ehdr_size, ph_size, phnum, 0, 0, 0))
        phdrs = b"".join(struct.pack("<8I", 1 if i == 2 else 0, offs[i], 0, 0x8000 * i,
                                     sizes[i], sizes[i], flags[i], 4)
                         for i in range(phnum))
        mdt = ehdr + phdrs + hash_seg   # Android .mdt = headers + hash segment
        with tempfile.TemporaryDirectory() as td:
            base = os.path.join(td, "adsp")
            with open(base + ".mdt", "wb") as f:
                f.write(mdt)
            with open(base + ".b01", "wb") as f:
                f.write(hash_seg)
            with open(base + ".b02", "wb") as f:
                f.write(load_seg)
            data, elf = qcom_fw.load_full_image(base + ".mdt")
            self.assertEqual(len(data), offs[2] + len(load_seg))
            self.assertEqual(data[offs[1]:offs[1] + len(hash_seg)], hash_seg)
            self.assertEqual(data[offs[2]:], load_seg)
            self.assertEqual([qcom_fw.Elf.seg_kind(p) for p in elf.phdrs], ["PHDR", "HASH", ""])
            self.assertIn("QC_IMAGE_VERSION_STRING=ADSP.TEST.1.0", qcom_fw.version_strings(data))


if __name__ == "__main__":
    unittest.main()
