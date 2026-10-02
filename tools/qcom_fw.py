#!/usr/bin/env python3
"""Inspect and repackage Qualcomm signed firmware (MBN / split MDT).

Mainline remoteproc drivers load a single squashed ELF (`adsp.mbn`) from
/lib/firmware/qcom/<soc>/<vendor>/<device>/. Android ships the same images
split into `adsp.mdt` + `adsp.b00`..`adsp.bNN` on the modem / vendor
partitions. This tool converts between the two and reports what an image is.

    python3 tools/qcom_fw.py info   adsp.mdt            # segments, hash seg, version strings
    python3 tools/qcom_fw.py squash adsp.mdt adsp.mbn   # .mdt + .bNN -> single .mbn
    python3 tools/qcom_fw.py scan   firmware_dir/       # summarise every image in a tree
    python3 tools/qcom_fw.py arb    xbl.img abl.img ... # anti-rollback (ARB) versions

The images keep their signatures. Squashing only rearranges bytes; it never
re-signs anything, and the device still verifies each image in TrustZone.
"""

import glob
import os
import re
import struct
import sys

PT_LOAD = 1
# Qualcomm puts the segment type in p_flags bits 24-26.
QCOM_SEG_TYPES = {0: "", 1: "", 2: "HASH", 3: "BOOT", 4: "L4BSP", 5: "SWAPPED",
                  6: "SWAP_POOL", 7: "PHDR"}
VERSION_RE = re.compile(rb"(QC_IMAGE_VERSION_STRING|OEM_IMAGE_VERSION_STRING|"
                        rb"OEM_IMAGE_UUID_STRING|IMAGE_VARIANT_STRING)=([\x20-\x7e]{1,200})")


class Elf:
    def __init__(self, data):
        if data[:4] != b"\x7fELF":
            raise ValueError("not an ELF")
        self.is64 = data[4] == 2
        endian = "<" if data[5] == 1 else ">"
        if self.is64:
            (self.e_type, self.machine, _v, self.entry, self.phoff, _shoff, _flags,
             self.ehsize, self.phentsize, self.phnum) = struct.unpack_from(
                endian + "HHIQQQIHHH", data, 16)
            fmt = endian + "IIQQQQQQ"
        else:
            (self.e_type, self.machine, _v, self.entry, self.phoff, _shoff, _flags,
             self.ehsize, self.phentsize, self.phnum) = struct.unpack_from(
                endian + "HHIIIIIHHH", data, 16)
            fmt = endian + "IIIIIIII"
        self.phdrs = []
        for i in range(self.phnum):
            raw = struct.unpack_from(fmt, data, self.phoff + i * self.phentsize)
            if self.is64:
                p_type, p_flags, off, vaddr, paddr, filesz, memsz, align = raw
            else:
                p_type, off, vaddr, paddr, filesz, memsz, p_flags, align = raw
            self.phdrs.append({"type": p_type, "flags": p_flags, "offset": off,
                               "vaddr": vaddr, "paddr": paddr, "filesz": filesz,
                               "memsz": memsz, "align": align})
        self.header_end = self.phoff + self.phnum * self.phentsize

    @staticmethod
    def seg_kind(ph):
        return QCOM_SEG_TYPES.get((ph["flags"] >> 24) & 7, "?")


def _segment_paths(mdt_path, n):
    stem = mdt_path[:-4]
    return [stem + ".b%02d" % i for i in range(n)]


def load_full_image(path):
    """Return the complete image bytes, reassembling .mdt + .bNN when needed."""
    with open(path, "rb") as f:
        head = f.read()
    elf = Elf(head)
    if not path.endswith(".mdt"):
        return head, elf
    size = max([elf.header_end] + [ph["offset"] + ph["filesz"] for ph in elf.phdrs])
    out = bytearray(size)
    out[:len(head)] = head[:size]
    for i, (ph, seg) in enumerate(zip(elf.phdrs, _segment_paths(path, elf.phnum))):
        if not ph["filesz"]:
            continue
        if os.path.exists(seg):
            with open(seg, "rb") as f:
                blob = f.read()
        elif ph["offset"] + ph["filesz"] <= len(head):
            blob = head[ph["offset"]:ph["offset"] + ph["filesz"]]
        else:
            raise FileNotFoundError("segment %d missing: %s" % (i, seg))
        if len(blob) != ph["filesz"]:
            raise ValueError("%s: expected %d bytes, got %d" % (seg, ph["filesz"], len(blob)))
        out[ph["offset"]:ph["offset"] + ph["filesz"]] = blob
    return bytes(out), elf


SW_ID_OU_RE = re.compile(rb"01 ([0-9A-Fa-f]{16}) SW_ID")


def rollback_info(data, elf):
    """Read the anti-rollback version from an image's hash segment.

    Layouts (from coreboot util/qualcomm/mbn_tools.py and qtestsign):
      * MBN v3/v5: no metadata. The attestation certificate carries an OU
        field "01 <16 hex> SW_ID", whose upper 32 bits are the rollback
        version and lower 32 bits the software ID.
      * MBN v7: a 16-word header (10 header words + 6 words of common
        metadata, word 12 = software_id), then the QTI metadata and the OEM
        metadata. Each metadata block starts with major, minor,
        anti_rollback_version.
    Returns a dict, or None when the image has no recognisable hash segment.
    """
    for ph in elf.phdrs:
        if Elf.seg_kind(ph) != "HASH":
            continue
        seg = data[ph["offset"]:ph["offset"] + ph["filesz"]]
        if len(seg) < 40:
            return None
        version = struct.unpack_from("<I", seg, 4)[0]
        info = {"mbn_version": version}
        if version in (3, 5):
            m = SW_ID_OU_RE.search(seg)
            if m:
                v = int(m.group(1), 16)
                info.update(sw_id=v & 0xFFFFFFFF, arb=v >> 32, source="cert SW_ID")
            return info
        if version == 7 and len(seg) >= 64:
            w = struct.unpack_from("<16I", seg, 0)
            meta_qti, meta_oem = w[3], w[4]
            info["sw_id"] = w[12]
            off = 64
            if meta_qti >= 12:
                info["arb_qti"] = struct.unpack_from("<I", seg, off + 8)[0]
            off += meta_qti
            if meta_oem >= 12:
                major, minor, arb = struct.unpack_from("<3I", seg, off)
                info.update(arb=arb, metadata="%d.%d" % (major, minor), source="OEM metadata")
            return info
        info["source"] = "unsupported MBN header version"
        return info
    return None


def version_strings(data):
    seen = []
    for m in VERSION_RE.finditer(data):
        s = "%s=%s" % (m.group(1).decode(), m.group(2).decode(errors="replace").strip())
        if s not in seen:
            seen.append(s)
    return seen


def cmd_info(path):
    data, elf = load_full_image(path)
    print("%s: ELF%d machine=0x%x entry=0x%x phnum=%d size=%d" % (
        path, 64 if elf.is64 else 32, elf.machine, elf.entry, elf.phnum, len(data)))
    for i, ph in enumerate(elf.phdrs):
        print("  [%2d] type=%-2d %-6s off=0x%08x paddr=0x%010x filesz=0x%08x memsz=0x%08x" % (
            i, ph["type"], Elf.seg_kind(ph), ph["offset"], ph["paddr"], ph["filesz"], ph["memsz"]))
    for ph in elf.phdrs:
        if Elf.seg_kind(ph) == "HASH":
            seg = data[ph["offset"]:ph["offset"] + ph["filesz"]]
            certs = seg.count(b"\x30\x82")
            print("  hash segment: %d bytes, ~%d DER objects (signature + cert chain)" % (
                len(seg), certs))
    rb = rollback_info(data, elf)
    if rb:
        print("  anti-rollback: %s" % _fmt_rollback(rb))
    for s in version_strings(data):
        print("  " + s)
    return 0


def _fmt_rollback(rb):
    parts = ["mbn v%d" % rb["mbn_version"]]
    if "sw_id" in rb:
        parts.append("sw_id=0x%x" % rb["sw_id"])
    if "arb" in rb:
        parts.append("ARB=%d" % rb["arb"])
    if "arb_qti" in rb:
        parts.append("QTI-ARB=%d" % rb["arb_qti"])
    parts.append("(%s)" % rb.get("source", "?"))
    return " ".join(parts)


def cmd_arb(paths):
    """Print the anti-rollback version of each boot-chain image."""
    rc = 0
    for p in paths:
        try:
            data, elf = load_full_image(p)
            rb = rollback_info(data, elf)
        except (ValueError, FileNotFoundError, struct.error) as e:
            print("%-28s ERROR %s" % (os.path.basename(p), e))
            rc = 1
            continue
        print("%-28s %s" % (os.path.basename(p), _fmt_rollback(rb) if rb else "no hash segment"))
    return rc


def cmd_squash(src, dst):
    data, _ = load_full_image(src)
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(dst, "wb") as f:
        f.write(data)
    print("squashed %s -> %s (%d bytes)" % (src, dst, len(data)))
    return 0


def cmd_scan(root):
    paths = sorted(set(glob.glob(os.path.join(root, "**", "*.mdt"), recursive=True) +
                       glob.glob(os.path.join(root, "**", "*.mbn"), recursive=True)))
    for p in paths:
        try:
            data, elf = load_full_image(p)
        except (ValueError, FileNotFoundError, struct.error) as e:
            print("%-60s  ERROR %s" % (os.path.relpath(p, root), e))
            continue
        vs = [s.split("=", 1)[1] for s in version_strings(data)
              if s.startswith("QC_IMAGE_VERSION_STRING")]
        print("%-60s  %9d B  %2d segs  %s" % (os.path.relpath(p, root), len(data),
                                             elf.phnum, vs[0] if vs else ""))
    return 0


def main(argv):
    if len(argv) >= 3 and argv[1] == "info":
        return cmd_info(argv[2])
    if len(argv) >= 4 and argv[1] == "squash":
        return cmd_squash(argv[2], argv[3])
    if len(argv) >= 3 and argv[1] == "scan":
        return cmd_scan(argv[2])
    if len(argv) >= 3 and argv[1] == "arb":
        return cmd_arb(argv[2:])
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
