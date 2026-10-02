#!/usr/bin/env python3
"""Extract partition images from an Android A/B OTA payload.bin.

OnePlus 13 full OTAs (OxygenOS / ColorOS) are A/B update_engine payloads.
This extractor carries its own protobuf decoder, so it needs nothing beyond
the Python standard library. The only optional extra is `zstandard`, for
payloads that use ZSTD operations.

    python3 tools/payload_extract.py --list   ota.zip
    python3 tools/payload_extract.py -o out/images ota.zip
    python3 tools/payload_extract.py -o out/images -p boot,vendor_boot,dtbo payload.bin

The input can be the OTA .zip itself; payload.bin is then read from inside
the zip without unpacking it to disk first. Only full (non-delta) payloads
are supported, because delta payloads need the source images.
"""

import argparse
import bz2
import hashlib
import io
import lzma
import os
import struct
import sys
import zipfile

# InstallOperation.Type
OP_NAMES = {
    0: "REPLACE", 1: "REPLACE_BZ", 2: "MOVE", 3: "BSDIFF", 4: "SOURCE_COPY",
    5: "SOURCE_BSDIFF", 6: "ZERO", 7: "DISCARD", 8: "REPLACE_XZ",
    9: "PUFFDIFF", 10: "BROTLI_BSDIFF", 11: "ZUCCHINI", 12: "LZ4DIFF_BSDIFF",
    13: "LZ4DIFF_PUFFDIFF", 14: "ZSTD",
}


# --------------------------------------------------------------------------
# Tiny protobuf wire-format decoder (enough for update_metadata.proto)
# --------------------------------------------------------------------------

def _varint(buf, pos):
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def pb_decode(buf):
    """Return {field_number: [values...]}; nested messages stay as bytes."""
    fields = {}
    pos = 0
    n = len(buf)
    while pos < n:
        key, pos = _varint(buf, pos)
        num, wt = key >> 3, key & 7
        if wt == 0:
            val, pos = _varint(buf, pos)
        elif wt == 1:
            val = struct.unpack_from("<Q", buf, pos)[0]
            pos += 8
        elif wt == 2:
            ln, pos = _varint(buf, pos)
            val = bytes(buf[pos:pos + ln])
            pos += ln
        elif wt == 5:
            val = struct.unpack_from("<I", buf, pos)[0]
            pos += 4
        else:
            raise ValueError("unsupported protobuf wire type %d" % wt)
        fields.setdefault(num, []).append(val)
    return fields


def _one(fields, num, default=None):
    v = fields.get(num)
    return v[0] if v else default


class Extent:
    def __init__(self, raw):
        f = pb_decode(raw)
        self.start_block = _one(f, 1, 0)
        self.num_blocks = _one(f, 2, 0)


class Operation:
    def __init__(self, raw):
        f = pb_decode(raw)
        self.type = _one(f, 1, 0)
        self.data_offset = _one(f, 2, 0)
        self.data_length = _one(f, 3, 0)
        self.src_extents = [Extent(e) for e in f.get(4, [])]
        self.dst_extents = [Extent(e) for e in f.get(6, [])]
        self.data_sha256 = _one(f, 8)


class Partition:
    def __init__(self, raw):
        f = pb_decode(raw)
        self.name = _one(f, 1, b"").decode()
        new_info = _one(f, 7)
        info = pb_decode(new_info) if new_info else {}
        self.size = _one(info, 1, 0)
        self.hash = _one(info, 2)
        self.ops = [Operation(o) for o in f.get(8, [])]


class Payload:
    MAGIC = b"CrAU"

    def __init__(self, fp):
        self.fp = fp
        hdr = fp.read(24)
        if hdr[:4] != self.MAGIC:
            raise ValueError("not a payload.bin (magic %r)" % hdr[:4])
        version, manifest_size = struct.unpack(">QQ", hdr[4:20])
        if version != 2:
            raise ValueError("unsupported payload version %d" % version)
        (sig_size,) = struct.unpack(">I", hdr[20:24])
        manifest = pb_decode(fp.read(manifest_size))
        self.data_start = 24 + manifest_size + sig_size
        self.block_size = _one(manifest, 3, 4096)
        self.minor_version = _one(manifest, 12, 0)
        self.security_patch = (_one(manifest, 18, b"") or b"").decode()
        self.partitions = [Partition(p) for p in manifest.get(13, [])]

    def read_blob(self, op):
        self.fp.seek(self.data_start + op.data_offset)
        return self.fp.read(op.data_length)


def _decompress(op, blob):
    t = op.type
    if t == 0:
        return blob
    if t == 1:
        return bz2.decompress(blob)
    if t == 8:
        return lzma.decompress(blob)
    if t == 14:
        try:
            import zstandard
        except ImportError:
            sys.exit("payload uses ZSTD operations: pip install zstandard")
        return zstandard.ZstdDecompressor().decompressobj().decompress(blob)
    raise ValueError("operation %s needs a source image (delta OTA?)" %
                     OP_NAMES.get(t, t))


def extract_partition(payload, part, out_path, verify=True):
    bs = payload.block_size
    with open(out_path, "wb") as out:
        out.truncate(part.size)
        for op in part.ops:
            if op.type in (6, 7):            # ZERO / DISCARD: already sparse zeros
                continue
            blob = payload.read_blob(op)
            if verify and op.data_sha256 and hashlib.sha256(blob).digest() != op.data_sha256:
                raise ValueError("%s: data hash mismatch" % part.name)
            data = _decompress(op, blob)
            pos = 0
            for ext in op.dst_extents:
                n = ext.num_blocks * bs
                out.seek(ext.start_block * bs)
                out.write(data[pos:pos + n])
                pos += n
    if verify and part.hash:
        h = hashlib.sha256()
        with open(out_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.digest() != part.hash:
            raise ValueError("%s: final image hash mismatch" % part.name)


def open_payload(path):
    if zipfile.is_zipfile(path):
        zf = zipfile.ZipFile(path)
        info = zf.getinfo("payload.bin")
        if info.compress_type != zipfile.ZIP_STORED:
            # Rare; fall back to reading it into memory.
            return Payload(io.BytesIO(zf.read(info)))
        raw = open(path, "rb")
        raw.seek(info.header_offset)
        lh = raw.read(30)
        name_len, extra_len = struct.unpack("<HH", lh[26:30])
        base = info.header_offset + 30 + name_len + extra_len
        return Payload(_OffsetFile(raw, base))
    return Payload(open(path, "rb"))


class _OffsetFile:
    """File view that starts at `base` (payload.bin stored inside a zip)."""

    def __init__(self, fp, base):
        self.fp, self.base = fp, base
        fp.seek(base)

    def seek(self, off, whence=0):
        assert whence == 0
        self.fp.seek(self.base + off)

    def read(self, n=-1):
        return self.fp.read(n)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("payload", help="payload.bin or OTA .zip")
    ap.add_argument("-o", "--out", default="out/images")
    ap.add_argument("-p", "--partitions", help="comma-separated list (default: all)")
    ap.add_argument("--list", action="store_true", help="only list partitions")
    ap.add_argument("--no-verify", action="store_true", help="skip SHA-256 checks")
    args = ap.parse_args()

    payload = open_payload(args.payload)
    print("payload: block_size=%d minor_version=%d spl=%s partitions=%d" % (
        payload.block_size, payload.minor_version, payload.security_patch or "?",
        len(payload.partitions)))

    if args.list:
        for p in payload.partitions:
            kinds = sorted({OP_NAMES.get(o.type, str(o.type)) for o in p.ops})
            print("  %-24s %12d bytes  ops=%s" % (p.name, p.size, ",".join(kinds)))
        return 0

    wanted = set(args.partitions.split(",")) if args.partitions else None
    os.makedirs(args.out, exist_ok=True)
    for p in payload.partitions:
        if wanted and p.name not in wanted:
            continue
        dst = os.path.join(args.out, p.name + ".img")
        print("  extracting %-24s -> %s" % (p.name, dst), flush=True)
        extract_partition(payload, p, dst, verify=not args.no_verify)
    if wanted:
        missing = wanted - {p.name for p in payload.partitions}
        if missing:
            print("warning: not in payload: %s" % ", ".join(sorted(missing)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
