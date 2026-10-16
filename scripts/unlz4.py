#!/usr/bin/env python3
"""unlz4.py - minimal LZ4 decompressor (frame format and legacy format), standard library only.
  python3 -I unlz4.py in.lz4 out
"""
import struct, sys


def block(src, out):
    i, n = 0, len(src)
    while i < n:
        tok = src[i]; i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]; i += 1; lit += b
                if b != 255:
                    break
        out += src[i:i + lit]; i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8); i += 2
        if off == 0:
            raise ValueError("zero offset")
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]; i += 1; ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - off
        if start < 0:
            raise ValueError("offset before start")
        if off >= ml:
            out += out[start:start + ml]
        else:
            for k in range(ml):
                out.append(out[start + k])


def decompress(d):
    out = bytearray()
    p = 0
    while p < len(d):
        magic = struct.unpack_from("<I", d, p)[0]
        if magic == 0x184D2204:                      # frame format
            flg, bd = d[p + 4], d[p + 5]
            p += 6
            if flg & 0x08: p += 8                    # content size
            if flg & 0x01: p += 4                    # dict id
            p += 1                                   # header checksum
            bchk = flg & 0x10
            while True:
                sz = struct.unpack_from("<I", d, p)[0]; p += 4
                if sz == 0:
                    break
                raw, sz = sz & 0x80000000, sz & 0x7FFFFFFF
                chunk = d[p:p + sz]; p += sz
                if raw: out += chunk
                else: block(chunk, out)
                if bchk: p += 4
            if flg & 0x04: p += 4                    # content checksum
        elif magic == 0x184C2102:                    # legacy format
            p += 4
            while p + 4 <= len(d):
                sz = struct.unpack_from("<I", d, p)[0]
                if sz == 0x184C2102 or sz == 0x184D2204:
                    break
                p += 4
                block(d[p:p + sz], out); p += sz
        elif 0x184D2A50 <= magic <= 0x184D2A5F:      # skippable frame
            p += 8 + struct.unpack_from("<I", d, p + 4)[0]
        elif d[p:].strip(b"\0") == b"":
            break
        else:
            raise ValueError(f"unknown magic {magic:#x} at {p}")
    return bytes(out)


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    out = decompress(data)
    open(sys.argv[2], "wb").write(out)
    print(f"{len(data)} -> {len(out)} bytes")
