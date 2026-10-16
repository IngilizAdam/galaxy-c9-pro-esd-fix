#!/usr/bin/env python3
"""
patch_esd.py - disable the panel ESD check in a Samsung/Qualcomm (msm8976, kernel 3.10)
Android boot image by turning the boolean property `qcom,esd-check-enabled` into FDT_NOP
tokens in every device tree blob found in the image.

Size-preserving: kernel, ramdisk and every section offset stay exactly the same; only the
12 bytes of each matching property header (+ its value, which is empty) become NOPs.

Usage:
  python3 -I patch_esd.py info    boot.img
  python3 -I patch_esd.py unpack  boot.img OUTDIR          # dump sections + each DTB
  python3 -I patch_esd.py patch   boot.img boot-patched.img [--prop NAME] [--tail seandroid|keep|none]
  python3 -I patch_esd.py verify  boot.img boot-patched.img   # prove only the intended bytes changed

Only standard library. Nothing from the firmware is executed.
"""
import argparse, hashlib, os, struct, sys

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9
DEFAULT_PROP = "qcom,esd-check-enabled"


def align(n, a):
    return (n + a - 1) // a * a


# ---------------------------------------------------------------- boot image (header v0, QC dt_size)
class BootImg:
    HDR_FMT = "<8s10I16s512s32s1024s"   # magic, k_size,k_addr, r_size,r_addr, s_size,s_addr, tags, page, dt_size, os_ver, name, cmdline, id, extra

    def __init__(self, data):
        if data[:8] != b"ANDROID!":
            sys.exit("not an Android boot image (no ANDROID! magic)")
        self.data = bytearray(data)
        f = struct.unpack_from(self.HDR_FMT, data, 0)
        (_, self.k_size, _, self.r_size, _, self.s_size, _, _, self.page,
         self.dt_size, self.os_ver, self.name, self.cmdline, self.id, _) = f
        if self.page not in (2048, 4096, 8192, 16384):
            sys.exit(f"unexpected page size {self.page}")
        p = self.page
        self.k_off = p
        self.r_off = self.k_off + align(self.k_size, p)
        self.s_off = self.r_off + align(self.r_size, p)
        self.dt_off = self.s_off + align(self.s_size, p)
        self.end = self.dt_off + align(self.dt_size, p)
        if self.end > len(data):
            sys.exit(f"header says image ends at {self.end}, file is only {len(data)} bytes")
        self.tail = bytes(data[self.end:])

    def section(self, name):
        off, size = {"kernel": (self.k_off, self.k_size), "ramdisk": (self.r_off, self.r_size),
                     "second": (self.s_off, self.s_size), "dt": (self.dt_off, self.dt_size)}[name]
        return off, size

    def sha1_id(self, include_dt):
        h = hashlib.sha1()
        for name in ("kernel", "ramdisk", "second") + (("dt",) if include_dt else ()):
            off, size = self.section(name)
            h.update(self.data[off:off + size])
            h.update(struct.pack("<I", size))
        return h.digest()

    def id_mode(self):
        """Which SHA1 recipe reproduces the stored id? 'qc' (with dt), 'aosp' (without), or None."""
        stored = self.id[:20]
        if self.sha1_id(True) == stored:
            return "qc"
        if self.sha1_id(False) == stored:
            return "aosp"
        return None

    def set_id(self, digest):
        id_off = struct.calcsize("<8s10I16s512s")
        self.data[id_off:id_off + 32] = digest.ljust(32, b"\0")


# ---------------------------------------------------------------- locating DTBs
def find_dtbs(img):
    """Return sorted unique (abs_offset, totalsize, where) of every valid FDT in dt section and kernel."""
    found = {}
    off, size = img.section("dt")
    if size:
        dt = bytes(img.data[off:off + size])
        if dt[:4] == b"QCDT":
            ver, n = struct.unpack_from("<II", dt, 4)
            words = {1: 5, 2: 6, 3: 10}.get(ver)
            if not words:
                print(f"  ! unknown QCDT version {ver}; falling back to magic scan")
            else:
                for i in range(n):
                    e = struct.unpack_from(f"<{words}I", dt, 12 + i * words * 4)
                    o, s = e[-2], e[-1]
                    found[off + o] = ("dt/QCDT", s)
        scan(img.data, off, size, found, "dt/scan")
    # DTBs appended to the kernel (zImage-dtb) when there is no dt section
    k_off, k_size = img.section("kernel")
    scan(img.data, k_off, k_size, found, "kernel-appended")
    out = []
    for o, (where, _) in sorted(found.items()):
        hdr = fdt_header(img.data, o)
        if hdr:
            out.append((o, hdr["totalsize"], where))
    return out


def scan(data, off, size, found, where):
    magic = struct.pack(">I", FDT_MAGIC)
    i = data.find(magic, off, off + size)
    while i != -1:
        h = fdt_header(data, i)
        if h and i + h["totalsize"] <= off + size:
            found.setdefault(i, (where, h["totalsize"]))
        i = data.find(magic, i + 4, off + size)


def fdt_header(data, o):
    if o + 40 > len(data):
        return None
    v = struct.unpack_from(">10I", data, o)
    keys = ("magic", "totalsize", "off_struct", "off_strings", "off_rsv", "version",
            "last_comp", "boot_cpuid", "size_strings", "size_struct")
    h = dict(zip(keys, v))
    if h["magic"] != FDT_MAGIC or not (16 <= h["version"] <= 17) or h["totalsize"] < 40:
        return None
    if h["off_struct"] >= h["totalsize"] or h["off_strings"] >= h["totalsize"]:
        return None
    return h


# ---------------------------------------------------------------- FDT walk / patch
def walk(data, base):
    """Yield (kind, path, prop_name, token_offset_abs, prop_len) for every node/prop."""
    h = fdt_header(data, base)
    s = base + h["off_struct"]
    strs = base + h["off_strings"]
    end = s + h["size_struct"] if h["version"] >= 17 else base + h["totalsize"]
    p, path = s, []
    while p < end:
        tok = struct.unpack_from(">I", data, p)[0]
        if tok == FDT_BEGIN_NODE:
            q = data.index(b"\0", p + 4)
            path.append(data[p + 4:q].decode("ascii", "replace"))
            p = align(q + 1 - base, 4) + base
        elif tok == FDT_END_NODE:
            path.pop()
            p += 4
        elif tok == FDT_PROP:
            ln, nameoff = struct.unpack_from(">II", data, p + 4)
            q = data.index(b"\0", strs + nameoff)
            name = data[strs + nameoff:q].decode("ascii", "replace")
            yield ("prop", "/" + "/".join(x for x in path if x), name, p, ln)
            p = p + 12 + align(ln, 4)
        elif tok == FDT_NOP:
            p += 4
        elif tok == FDT_END:
            return
        else:
            raise ValueError(f"bad FDT token {tok:#x} at {p - base:#x} in DTB @ {base:#x}")


def patch_dtb(data, base, prop):
    hits = []
    for kind, path, name, tokoff, ln in list(walk(data, base)):
        if name == prop:
            n = 12 + align(ln, 4)
            data[tokoff:tokoff + n] = struct.pack(">I", FDT_NOP) * (n // 4)
            hits.append((path, tokoff, n))
    return hits


def dtb_model(data, base):
    out = {}
    for kind, path, name, tokoff, ln in walk(data, base):
        if path == "/" and name in ("model",):
            v = bytes(data[tokoff + 12:tokoff + 12 + ln]).rstrip(b"\0").decode("ascii", "replace")
            out[name] = v
        if path == "/" and name in ("qcom,board-id", "qcom,msm-id"):
            out[name] = struct.unpack_from(f">{ln // 4}I", data, tokoff + 12)
    return out


# ---------------------------------------------------------------- commands
def cmd_info(a):
    img = BootImg(open(a.image, "rb").read())
    print(f"page={img.page} kernel={img.k_size} ramdisk={img.r_size} second={img.s_size} dt={img.dt_size}")
    name = img.name.rstrip(b"\0")
    cmdline = img.cmdline.rstrip(b"\0").decode(errors="replace")
    print(f"name={name!r}")
    print(f"cmdline={cmdline}")
    print(f"id sha1 recipe matches: {img.id_mode()}")
    print(f"trailing bytes after last section: {len(img.tail)} (starts {img.tail[:16]!r})")
    dtbs = find_dtbs(img)
    print(f"DTBs found: {len(dtbs)}")
    for o, sz, where in dtbs:
        m = dtb_model(img.data, o)
        props = [(p, ln) for k, p, n, t, ln in walk(img.data, o) if n == a.prop]
        print(f"  @{o:#x} size={sz} [{where}] {m.get('model', '?')} board-id={m.get('qcom,board-id')}"
              f" -> {a.prop}: {len(props)} at {[p for p, _ in props]}")


def cmd_unpack(a):
    img = BootImg(open(a.image, "rb").read())
    os.makedirs(a.outdir, exist_ok=True)
    for name in ("kernel", "ramdisk", "second", "dt"):
        off, size = img.section(name)
        if size:
            open(os.path.join(a.outdir, name), "wb").write(img.data[off:off + size])
    open(os.path.join(a.outdir, "tail"), "wb").write(img.tail)
    for i, (o, sz, where) in enumerate(find_dtbs(img)):
        open(os.path.join(a.outdir, f"dtb_{i:02d}_{o:#x}.dtb"), "wb").write(img.data[o:o + sz])
    print(f"written to {a.outdir}/ (decompile with: dtc -I dtb -O dts -o x.dts dtb_NN_*.dtb)")


def cmd_patch(a):
    raw = open(a.image, "rb").read()
    img = BootImg(raw)
    mode = img.id_mode()
    total = 0
    for o, sz, where in find_dtbs(img):
        hits = patch_dtb(img.data, o, a.prop)
        for path, tokoff, n in hits:
            print(f"  DTB @{o:#x} [{where}]: NOP'd {a.prop} in {path} ({n} bytes @ {tokoff:#x})")
        total += len(hits)
    if total == 0:
        sys.exit(f"ERROR: property {a.prop!r} not found in any DTB - nothing written")
    if mode:
        img.set_id(img.sha1_id(mode == "qc"))
        print(f"  header id recomputed ({mode} recipe)")
    else:
        print("  ! original header id matched no known recipe; left unchanged")
    body = bytes(img.data[:img.end])
    tail = {"keep": img.tail, "none": b"", "seandroid": b"SEANDROIDENFORCE"}[a.tail]
    open(a.out, "wb").write(body + tail)
    print(f"patched {total} occurrence(s) -> {a.out} ({len(body + tail)} bytes, tail={a.tail})")


def cmd_verify(a):
    A = BootImg(open(a.orig, "rb").read())
    B = BootImg(open(a.patched, "rb").read())
    ok = True
    # byte ranges of the target property in the ORIGINAL image: the only bytes allowed to change
    allowed = set()
    for o, sz, where in find_dtbs(A):
        for kind, path, name, tokoff, ln in walk(A.data, o):
            if name == a.prop:
                allowed.update(range(tokoff, tokoff + 12 + align(ln, 4)))
    for name in ("kernel", "ramdisk", "second"):
        if A.section(name) != B.section(name):
            print(f"FAIL {name}: layout differs"); ok = False; continue
        o, s = A.section(name)
        d = [i for i in range(o, o + s) if A.data[i] != B.data[i]] if A.data[o:o + s] != B.data[o:o + s] else []
        stray = [i for i in d if i not in allowed]
        sha = hashlib.sha256(A.data[o:o + s]).hexdigest()[:16]
        if not d:
            print(f"ok   {name}: sha256 {sha} identical")
        elif not stray:
            print(f"ok   {name}: {len(d)} bytes changed, all inside DTBs appended to it (target property only)")
        else:
            print(f"FAIL {name}: {len(stray)} bytes changed outside the target property"); ok = False
    hdr_len = struct.calcsize(BootImg.HDR_FMT)
    id_off = struct.calcsize("<8s10I16s512s")
    hdr_diff = [i for i in range(hdr_len) if A.data[i] != B.data[i] and not (id_off <= i < id_off + 32)]
    print(f"{'ok  ' if not hdr_diff else 'FAIL'} header (excluding id): {len(hdr_diff)} differing bytes")
    ok &= not hdr_diff
    lo, hi = A.k_off, A.end
    diff = [i for i in range(lo, hi) if A.data[i] != B.data[i]]
    stray = [i for i in diff if i not in allowed]
    print(f"{'ok  ' if not stray else 'FAIL'} section bytes changed: {len(diff)}, outside target property: {len(stray)}")
    ok &= not stray
    left = sum(1 for o, sz, w in find_dtbs(B) for k, p, n, t, l in walk(B.data, o) if n == a.prop)
    print(f"{'ok  ' if left == 0 else 'FAIL'} remaining {a.prop}: {left}")
    ok &= left == 0
    for o, sz, w in find_dtbs(B):
        list(walk(B.data, o))  # raises if structure broken
    print("ok   all patched DTBs still parse")
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info"); p.add_argument("image"); p.add_argument("--prop", default=DEFAULT_PROP)
    p.set_defaults(fn=cmd_info)
    p = sub.add_parser("unpack"); p.add_argument("image"); p.add_argument("outdir"); p.set_defaults(fn=cmd_unpack)
    p = sub.add_parser("patch"); p.add_argument("image"); p.add_argument("out")
    p.add_argument("--prop", default=DEFAULT_PROP)
    p.add_argument("--tail", choices=("seandroid", "keep", "none"), default="seandroid")
    p.set_defaults(fn=cmd_patch)
    p = sub.add_parser("verify"); p.add_argument("orig"); p.add_argument("patched")
    p.add_argument("--prop", default=DEFAULT_PROP); p.set_defaults(fn=cmd_verify)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
