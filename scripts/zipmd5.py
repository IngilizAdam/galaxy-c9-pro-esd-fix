#!/usr/bin/env python3
"""zipmd5.py ZIP - verify the md5 trailer of every *.tar.md5 inside ZIP and list its members, streaming."""
import hashlib, sys, tarfile, zipfile

z = zipfile.ZipFile(sys.argv[1])
for info in z.infolist():
    if not info.filename.endswith(".tar.md5"):
        continue
    h = hashlib.md5()
    with z.open(info) as f:
        tail = b""
        while True:
            b = f.read(1 << 22)
            if not b:
                break
            buf = tail + b
            h_part, tail = buf[:-256], buf[-256:]
            h.update(h_part)
    nl = tail.rstrip(b"\n").rfind(b"\n")      # trailer = last line "md5  name\n"
    body_end, trailer = tail[:nl + 1], tail[nl + 1:]
    h.update(body_end)
    want = trailer.split()[0].decode()
    print(f"{'OK  ' if h.hexdigest() == want else 'FAIL'} md5 {info.filename}")
    with z.open(info) as f, tarfile.open(fileobj=f, mode="r|") as t:
        for m in t:
            print(f"      {m.size:>12}  {m.name}")
