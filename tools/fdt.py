#!/usr/bin/env python3
"""Minimal, dependency-free Flattened Device Tree (DTB/DTBO) parser.

Used by the other tools to inspect device trees pulled out of OnePlus 13
vendor_boot / dtbo images without needing dtc installed.

    python3 tools/fdt.py dump  file.dtb          # decompile to DTS-like text
    python3 tools/fdt.py split blob.bin outdir/  # carve concatenated DTBs
"""

import os
import struct
import sys

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9


class Node:
    def __init__(self, name, parent=None):
        self.name = name
        self.parent = parent
        self.props = {}       # name -> bytes, insertion ordered
        self.children = []

    @property
    def path(self):
        if self.parent is None:
            return "/"
        parent = self.parent.path
        return (parent if parent.endswith("/") else parent + "/") + self.name

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def child(self, name):
        for c in self.children:
            if c.name == name:
                return c
        return None

    def find(self, path):
        node = self
        for part in [p for p in path.split("/") if p]:
            node = node.child(part)
            if node is None:
                return None
        return node

    # -- typed property accessors ------------------------------------------

    def str(self, name, default=None):
        v = self.props.get(name)
        if v is None:
            return default
        return v.rstrip(b"\0").decode("utf-8", "replace")

    def strlist(self, name):
        v = self.props.get(name)
        if not v:
            return []
        return [s.decode("utf-8", "replace") for s in v.rstrip(b"\0").split(b"\0")]

    def cells(self, name):
        v = self.props.get(name)
        if v is None or len(v) % 4:
            return None
        return list(struct.unpack(">%dI" % (len(v) // 4), v))

    def enabled(self):
        return self.str("status", "okay") in ("okay", "ok")


class FDT:
    def __init__(self, data):
        if len(data) < 40:
            raise ValueError("too short for an FDT header")
        (magic, totalsize, off_struct, off_strings, off_rsv, version,
         _last_comp, boot_cpuid, size_strings, size_struct) = struct.unpack(
            ">10I", data[:40])
        if magic != FDT_MAGIC:
            raise ValueError("bad FDT magic 0x%08x" % magic)
        self.totalsize = totalsize
        self.version = version
        self.boot_cpuid = boot_cpuid
        self.data = data[:totalsize]
        self.reserved = []
        off = off_rsv
        while True:
            addr, size = struct.unpack(">QQ", self.data[off:off + 16])
            off += 16
            if addr == 0 and size == 0:
                break
            self.reserved.append((addr, size))
        strings = self.data[off_strings:off_strings + size_strings]
        self.root = self._parse(self.data[off_struct:off_struct + size_struct], strings)

    @staticmethod
    def _parse(blob, strings):
        def getstr(off):
            end = strings.index(b"\0", off)
            return strings[off:end].decode("utf-8", "replace")

        root = None
        node = None
        pos = 0
        while pos < len(blob):
            (tok,) = struct.unpack(">I", blob[pos:pos + 4])
            pos += 4
            if tok == FDT_BEGIN_NODE:
                end = blob.index(b"\0", pos)
                name = blob[pos:end].decode("utf-8", "replace")
                pos = (end + 4) & ~3
                new = Node(name, node)
                if node is None:
                    root = new
                else:
                    node.children.append(new)
                node = new
            elif tok == FDT_END_NODE:
                node = node.parent
            elif tok == FDT_PROP:
                length, nameoff = struct.unpack(">II", blob[pos:pos + 8])
                pos += 8
                node.props[getstr(nameoff)] = blob[pos:pos + length]
                pos = (pos + length + 3) & ~3
            elif tok == FDT_NOP:
                continue
            elif tok == FDT_END:
                break
            else:
                raise ValueError("bad FDT token %d at struct offset %d" % (tok, pos - 4))
        return root

    # -- convenience -------------------------------------------------------

    def phandles(self):
        """Map phandle number -> Node."""
        out = {}
        for n in self.root.walk():
            for key in ("phandle", "linux,phandle"):
                c = n.cells(key)
                if c:
                    out[c[0]] = n
        return out

    def labels(self):
        """Map label -> path, from the /__symbols__ node if present."""
        sym = self.root.child("__symbols__")
        if not sym:
            return {}
        return {k: sym.str(k) for k in sym.props}

    def is_overlay(self):
        return self.root.child("__fixups__") is not None or any(
            c.child("__overlay__") for c in self.root.children)


def find_fdts(data):
    """Yield (offset, FDT) for every FDT embedded in an arbitrary blob."""
    magic = struct.pack(">I", FDT_MAGIC)
    pos = 0
    while True:
        pos = data.find(magic, pos)
        if pos < 0:
            return
        try:
            fdt = FDT(data[pos:])
        except (ValueError, struct.error, IndexError):
            pos += 4
            continue
        yield pos, fdt
        pos += max(fdt.totalsize, 4)


def _fmt_value(name, v):
    if not v:
        return None
    # printable NUL separated strings?
    if v[-1:] == b"\0" and b"\0\0" not in v and v[0:1] != b"\0":
        try:
            parts = v[:-1].decode("ascii").split("\0")
            if all(p and all(32 <= ord(ch) < 127 for ch in p) for p in parts):
                return ", ".join('"%s"' % p.replace('"', '\\"') for p in parts)
        except UnicodeDecodeError:
            pass
    if len(v) % 4 == 0:
        cells = struct.unpack(">%dI" % (len(v) // 4), v)
        return "<" + " ".join("0x%x" % c for c in cells) + ">"
    return "[" + " ".join("%02x" % b for b in v) + "]"


def to_dts(fdt):
    out = ["/dts-v1/;", ""]
    for addr, size in fdt.reserved:
        out.append("/memreserve/ 0x%x 0x%x;" % (addr, size))

    def emit(node, depth):
        ind = "\t" * depth
        out.append("%s%s {" % (ind, node.name or "/"))
        for k, v in node.props.items():
            val = _fmt_value(k, v)
            out.append("%s\t%s%s;" % (ind, k, "" if val is None else " = " + val))
        for c in node.children:
            out.append("")
            emit(c, depth + 1)
        out.append("%s};" % ind)

    emit(fdt.root, 0)
    return "\n".join(out) + "\n"


def main(argv):
    if len(argv) < 3 or argv[1] not in ("dump", "split"):
        print(__doc__)
        return 1
    with open(argv[2], "rb") as f:
        data = f.read()
    if argv[1] == "dump":
        found = list(find_fdts(data))
        if not found:
            print("no FDT found", file=sys.stderr)
            return 1
        for off, fdt in found:
            if len(found) > 1:
                print("// ---- FDT at offset 0x%x (%d bytes) ----" % (off, fdt.totalsize))
            sys.stdout.write(to_dts(fdt))
        return 0
    outdir = argv[3] if len(argv) > 3 else "."
    os.makedirs(outdir, exist_ok=True)
    for i, (off, fdt) in enumerate(find_fdts(data)):
        model = fdt.root.str("model", "")
        path = os.path.join(outdir, "%02d.dtb" % i)
        with open(path, "wb") as f:
            f.write(fdt.data)
        print("%s  off=0x%08x size=%-7d %s" % (path, off, fdt.totalsize, model))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
