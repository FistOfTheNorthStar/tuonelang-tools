"""Capture what zlib and zipfile do, as the oracle for the `inflate` and
`zip` ports.

    ../shallowflaws/.venv/bin/python examples/zip_oracle.py

needs `zip` and `ditto` on PATH (macOS has both) for two archives written by
tools other than Python.

The inflate half hands CPython's `zlib.decompress` (zlib 1.2.12) streams
of three kinds: what `zlib` itself compresses — deterministic text,
runs, and noise at every level and strategy, raw and zlib-wrapped, with
large and small windows — streams written bit by bit to reach the
corners zlib's compressor never emits (the longest match at the farthest
distance, a single distance code, a stored block of length zero), and
malformed streams, one for each error `inflate` can report. (Decoding
with a window smaller than 32 KiB is left out: whether zlib notices a
distance past it depends on where CPython's output buffer happened to be
split.) For each it
records the output (its length, CRC-32, and — when short — the bytes) or
the `zlib.error` message.
"""

import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import warnings
import zipfile
import zlib

warnings.simplefilter("ignore")

here = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Deterministic inputs.
# ---------------------------------------------------------------------------

class Lcg:
    def __init__(self, seed):
        self.x = seed

    def next(self):
        self.x = (self.x * 1103515245 + 12345) % 2147483648
        return self.x >> 16


WORDS = ("the of and to in is was for on that with as by at from his it an were are which this be "
         "or has had first one their its new after but who not they have her she two been other "
         "when there all during into school time may years more most only over city some world "
         "would where later up such used many can state about national out known university united "
         "then made").split()


def text(n, seed):
    r = Lcg(seed)
    out = bytearray()
    k = 0
    while len(out) < n:
        out += WORDS[r.next() % len(WORDS)].encode()
        k += 1
        out += b"\n" if k % 13 == 0 else b" "
    return bytes(out[:n])


def noise(n, seed):
    r = Lcg(seed)
    return bytes(r.next() & 255 for _ in range(n))


def runs(n, seed):
    r = Lcg(seed)
    out = bytearray()
    while len(out) < n:
        out += bytes([r.next() & 255]) * (1 + r.next() % 700)
    return bytes(out[:n])


def raw(data, level=6, strategy=zlib.Z_DEFAULT_STRATEGY, wbits=-15, memlevel=8):
    c = zlib.compressobj(level, zlib.DEFLATED, wbits, memlevel, strategy)
    return c.compress(data) + c.flush()


# ---------------------------------------------------------------------------
# A bit writer, for streams zlib would never write.
# ---------------------------------------------------------------------------

class Bits:
    def __init__(self):
        self.bits = []

    def put(self, value, n):
        """`n` bits of `value`, least significant first (header fields, extras)."""
        for i in range(n):
            self.bits.append((value >> i) & 1)

    def code(self, code, n):
        """A Huffman code, most significant bit first."""
        for i in reversed(range(n)):
            self.bits.append((code >> i) & 1)

    def align(self):
        while len(self.bits) % 8:
            self.bits.append(0)

    def byte(self, b):
        self.put(b, 8)

    def data(self):
        out = bytearray()
        for i in range(0, len(self.bits), 8):
            chunk = self.bits[i:i + 8]
            out.append(sum(b << k for k, b in enumerate(chunk)))
        return bytes(out)


def fixed_lit(w, sym):
    if sym < 144:
        w.code(0x30 + sym, 8)
    elif sym < 256:
        w.code(0x190 + sym - 144, 9)
    elif sym < 280:
        w.code(sym - 256, 7)
    else:
        w.code(0xC0 + sym - 280, 8)


LEN_BASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115,
            131, 163, 195, 227, 258]
LEN_EXTRA = [0] * 8 + [1] * 4 + [2] * 4 + [3] * 4 + [4] * 4 + [5] * 4 + [0]
DIST_BASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537,
             2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577]
DIST_EXTRA = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13]


def fixed_match(w, length, dist):
    i = max(k for k in range(29) if LEN_BASE[k] <= length)
    if length == 258:
        i = 28
    fixed_lit(w, 257 + i)
    w.put(length - LEN_BASE[i], LEN_EXTRA[i])
    d = max(k for k in range(30) if DIST_BASE[k] <= dist)
    w.code(d, 5)
    w.put(dist - DIST_BASE[d], DIST_EXTRA[d])


CL_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]


def canonical(lengths):
    """Canonical codes for `lengths`, as RFC 1951 §3.2.2 assigns them."""
    maxlen = max(lengths) if lengths else 0
    count = [0] * (maxlen + 1)
    for n in lengths:
        if n:
            count[n] += 1
    code, nxt = 0, [0] * (maxlen + 2)
    for bits in range(1, maxlen + 1):
        code = (code + count[bits - 1]) << 1
        nxt[bits] = code
    codes = []
    for n in lengths:
        if n:
            codes.append(nxt[n])
            nxt[n] += 1
        else:
            codes.append(None)
    return codes


def dynamic_header(w, final, lit_lengths, dist_lengths, cl_lengths=None, cl_symbols=None, hlit=None, hdist=None):
    """A dynamic block header. The code lengths are sent as literal lengths
    (symbols 0-15) unless `cl_symbols` gives the sequence to send, as
    (symbol, extra-bits value) pairs."""
    w.put(final, 1)
    w.put(2, 2)
    seq = cl_symbols if cl_symbols is not None else [(n, None) for n in lit_lengths + dist_lengths]
    if cl_lengths is None:
        # A complete code over the symbols used: lengths 1, 2, …, k-1, k-1.
        used = sorted({s for s, _ in seq})
        if len(used) == 1:
            used.append(18 if used[0] != 18 else 17)
        cl_lengths = [0] * 19
        for i, s in enumerate(used):
            cl_lengths[s] = min(i + 1, len(used) - 1)
    nlit = hlit if hlit is not None else len(lit_lengths) - 257
    ndist = hdist if hdist is not None else len(dist_lengths) - 1
    w.put(nlit, 5)
    w.put(ndist, 5)
    hclen = 19
    while hclen > 4 and cl_lengths[CL_ORDER[hclen - 1]] == 0:
        hclen -= 1
    w.put(hclen - 4, 4)
    for i in range(hclen):
        w.put(cl_lengths[CL_ORDER[i]], 3)
    cl_codes = canonical(cl_lengths)
    extra = {16: 2, 17: 3, 18: 7}
    for s, x in seq:
        w.code(cl_codes[s], cl_lengths[s])
        if s in extra:
            w.put(x, extra[s])
    return canonical(lit_lengths), canonical(dist_lengths)


def simple_lengths():
    """Literal/length lengths for 'a', 'b', end-of-block, and length 3; one distance."""
    lit = [0] * 258
    lit[ord("a")] = 2
    lit[ord("b")] = 2
    lit[256] = 2
    lit[257] = 2
    return lit


def crafted():
    cases = []

    def add(name, data, wbits=-15):
        cases.append((name, data, wbits))

    w = Bits(); w.put(1, 1); w.put(3, 2)
    add("block_type_3", w.data())

    w = Bits(); w.put(1, 1); w.put(0, 2); w.align(); w.put(5, 16); w.put(0, 16)
    add("stored_lengths_mismatch", w.data())

    w = Bits(); w.put(1, 1); w.put(0, 2); w.align(); w.put(5, 16); w.put(0xFFFF ^ 5, 16); w.byte(65); w.byte(66)
    add("stored_truncated", w.data())

    w = Bits(); w.put(1, 1); w.put(0, 2); w.align(); w.put(0, 16); w.put(0xFFFF, 16)
    add("stored_empty", w.data())

    w = Bits(); w.put(0, 1); w.put(0, 2); w.align(); w.put(3, 16); w.put(0xFFFF ^ 3, 16)
    for b in b"abc":
        w.byte(b)
    w.put(1, 1); w.put(1, 2); fixed_lit(w, ord("d")); fixed_match(w, 6, 4); fixed_lit(w, 256)
    add("stored_then_fixed", w.data())

    w = Bits(); w.put(1, 1); w.put(1, 2); fixed_lit(w, 286)
    add("fixed_litlen_286", w.data())

    w = Bits(); w.put(1, 1); w.put(1, 2); fixed_lit(w, 287)
    add("fixed_litlen_287", w.data())

    w = Bits(); w.put(1, 1); w.put(1, 2); fixed_lit(w, 65); fixed_lit(w, 257); w.code(30, 5)
    add("fixed_distance_30", w.data())

    w = Bits(); w.put(1, 1); w.put(1, 2); fixed_lit(w, 65); fixed_lit(w, 257); w.code(31, 5)
    add("fixed_distance_31", w.data())

    w = Bits(); w.put(1, 1); w.put(1, 2); fixed_lit(w, 65); fixed_match(w, 3, 2); fixed_lit(w, 256)
    add("distance_too_far", w.data())

    w = Bits(); w.put(1, 1); w.put(1, 2); fixed_lit(w, 65); fixed_match(w, 258, 1); fixed_match(w, 258, 1); fixed_lit(w, 256)
    add("overlapping_copy", w.data())

    # 251 literals, the pattern repeated to 32768 bytes, then a copy from
    # the very start of the window: the farthest distance, the longest match.
    w = Bits(); w.put(1, 1); w.put(1, 2)
    for i in range(251):
        fixed_lit(w, i)
    total = 251
    while total < 32768:
        n = min(258, 32768 - total)
        if n < 3:
            break
        fixed_match(w, n, 251)
        total += n
    while total < 32768:
        fixed_lit(w, 0)
        total += 1
    fixed_match(w, 258, 32768)
    fixed_match(w, 258, 32768)
    fixed_lit(w, 256)
    add("farthest_distance_longest_match", w.data())


    lit = simple_lengths()
    for final_bits in [(30, None), (None, 30)]:
        w = Bits()
        hl, hd = final_bits
        dynamic_header(w, 1, lit, [1], hlit=hl, hdist=hd)
        add("too_many_symbols_hlit" if hl else "too_many_symbols_hdist", w.data())

    w = Bits()
    cl = [0] * 19
    cl[0] = 1  # a lone code of length 1: incomplete, refused for code lengths
    dynamic_header(w, 1, lit, [1], cl_lengths=cl, cl_symbols=[(0, None)])
    add("code_lengths_incomplete", w.data())

    w = Bits()
    cl = [1] * 19  # 19 codes of length 1: over-subscribed
    dynamic_header(w, 1, lit, [1], cl_lengths=cl, cl_symbols=[])
    add("code_lengths_oversubscribed", w.data())

    w = Bits()
    dynamic_header(w, 1, lit, [1], cl_symbols=[(16, 0)])
    add("repeat_with_nothing_before", w.data())

    w = Bits()
    seq = [(2, None)] + [(16, 2)] * 60
    dynamic_header(w, 1, lit, [1], cl_symbols=seq)
    add("repeat_overflows", w.data())

    w = Bits()
    over = [0] * 258
    for s in (97, 98, 99, 256, 257):
        over[s] = 2
    dynamic_header(w, 1, over, [1])
    add("litlen_oversubscribed", w.data())

    w = Bits()
    inc = [0] * 258
    for s in (97, 256, 257):
        inc[s] = 2
    dynamic_header(w, 1, inc, [1])
    add("litlen_incomplete", w.data())

    w = Bits()
    noend = [0] * 258
    for s in (97, 98, 99, 257):
        noend[s] = 2
    dynamic_header(w, 1, noend, [1])
    add("missing_end_of_block", w.data())

    w = Bits()
    dynamic_header(w, 1, lit, [1, 1, 1])
    add("distances_oversubscribed", w.data())

    w = Bits()
    dynamic_header(w, 1, lit, [0, 2, 2])
    add("distances_incomplete", w.data())

    # One distance code of length 1 — incomplete, and allowed.
    w = Bits()
    lc, dc = dynamic_header(w, 1, lit, [1])
    w.code(lc[97], 2); w.code(lc[98], 2); w.code(lc[257], 2); w.code(dc[0], 1)
    w.code(lc[256], 2)
    add("single_distance_code", w.data())

    # No distance codes at all: fine until a match needs one.
    w = Bits()
    lc, dc = dynamic_header(w, 1, lit, [0])
    w.code(lc[97], 2); w.code(lc[256], 2)
    add("no_distance_codes", w.data())

    w = Bits()
    lc, dc = dynamic_header(w, 1, lit, [0])
    w.code(lc[97], 2); w.code(lc[257], 2); w.put(0, 1)
    add("no_distance_codes_used", w.data())

    # One literal/length code of length 1 besides end-of-block: complete.
    w = Bits()
    two = [0] * 257
    two[ord("z")] = 1
    two[256] = 1
    lc, dc = dynamic_header(w, 1, two, [1])
    for _ in range(5):
        w.code(lc[ord("z")], 1)
    w.code(lc[256], 1)
    add("two_symbol_litlen", w.data())

    # Code-length repeats of every kind: 18, 16 three times, 17.
    w = Bits()
    seq = [(18, 86)] + [(4, None), (16, 3), (16, 1), (16, 0)] + [(18, 127), (17, 4)] + [(4, None), (4, None)] + [(1, None)]
    rep = [0] * 97 + [4] * 14 + [0] * 145 + [4, 4]
    lc, dc = dynamic_header(w, 1, rep, [1], cl_symbols=seq)
    for ch in b"abcdefghijklmn":
        w.code(lc[ch], 4)
    w.code(lc[257], 4); w.code(dc[0], 1)
    w.code(lc[256], 4)
    add("code_length_repeats", w.data())

    # A dynamic block zlib wrote, cut short in the middle of its header.
    d = raw(text(3000, 7), 9)
    add("dynamic_header_truncated", d[:20])
    add("truncated_mid_data", d[:len(d) // 2])
    add("truncated_before_end", d[:-1])
    add("empty_input", b"")
    add("trailing_bytes_ignored", raw(b"hello, hello, hello") + b"\x00garbage")

    # zlib's wrapper (RFC 1950).
    good = zlib.compress(b"wrapped", 6)
    add("zlib_ok", good, 15)
    add("zlib_bad_check", bytes([good[0], good[1] ^ 1]) + good[2:], 15)
    cm7 = 0x77 << 8
    cm7 |= (31 - cm7 % 31) % 31
    add("zlib_method_7", bytes([cm7 >> 8, cm7 & 255]) + good[2:], 15)
    wb = 0x88 << 8
    wb |= (31 - wb % 31) % 31
    add("zlib_window_too_big", bytes([wb >> 8, wb & 255]) + good[2:], 15)
    dic = (0x78 << 8) | 0x20
    dic |= (31 - dic % 31) % 31
    add("zlib_preset_dictionary", bytes([dic >> 8, dic & 255]) + b"\x00\x00\x00\x01" + good[2:], 15)
    add("zlib_bad_adler", good[:-1] + bytes([good[-1] ^ 0x55]), 15)
    add("zlib_truncated_adler", good[:-2], 15)
    small = zlib.compress(b"small window " * 20, 6)
    sw = zlib.compressobj(6, zlib.DEFLATED, 9)
    small9 = sw.compress(b"small window " * 20) + sw.flush()
    add("zlib_window_9", small9, 15)
    add("zlib_window_15_into_9", small, 9)
    return cases


def generated():
    cases = []
    inputs = [
        ("empty", b""),
        ("one_byte", b"a"),
        ("text_1000", text(1000, 1)),
        ("text_20000", text(20000, 2)),
        ("text_100000", text(100000, 3)),
        ("noise_3000", noise(3000, 4)),
        ("runs_40000", runs(40000, 5)),
        ("all_bytes", bytes(range(256)) * 12),
    ]
    for name, data in inputs:
        for level in (0, 1, 6, 9):
            if level == 0 and len(data) > 20000:
                continue
            if name == "text_100000" and level != 6:
                continue
            cases.append((f"{name}_level{level}", raw(data, level), -15))
    cases.append(("text_66000_level0", raw(text(66000, 6), 0), -15))
    t = text(20000, 2)
    for sname, s in [("filtered", zlib.Z_FILTERED), ("huffman_only", zlib.Z_HUFFMAN_ONLY),
                     ("rle", zlib.Z_RLE), ("fixed", zlib.Z_FIXED)]:
        cases.append((f"text_20000_{sname}", raw(t, 6, s), -15))
        if sname != "huffman_only":
            cases.append((f"runs_40000_{sname}", raw(runs(40000, 5), 6, s), -15))
    cases.append(("text_20000_window9", raw(t, 9, wbits=-9), -15))
    cases.append(("text_20000_memlevel1", raw(t, 9, memlevel=1), -15))
    cases.append(("text_20000_zlib", zlib.compress(t, 9), 15))
    cases.append(("text_100000_zlib", zlib.compress(text(100000, 3), 6), 15))
    return cases


out = []
for name, data, wbits in generated() + crafted():
    rec = {"name": name, "wbits": wbits, "input": data.hex()}
    try:
        res = zlib.decompress(data, wbits)
        rec.update(ok=True, length=len(res), crc=zlib.crc32(res), output=res.hex() if len(res) <= 512 else "")
    except zlib.error as e:
        rec.update(ok=False, error=str(e))
    out.append(rec)
    print(name, rec.get("length", rec.get("error")))

print(len(out), "streams")


# ---------------------------------------------------------------------------
# Archives.
# ---------------------------------------------------------------------------

def zipfile_bytes(build, **kw):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", **kw) as zf:
        build(zf)
    return buf.getvalue()


def member(zf, name, data, method=zipfile.ZIP_DEFLATED, level=None, comment=b"", date=(2026, 9, 29, 12, 30, 44)):
    info = zipfile.ZipInfo(name, date_time=date)
    info.compress_type = method
    if level is not None:
        info.compress_level = level
    info.comment = comment
    zf.writestr(info, data)


def basic(zf):
    member(zf, "readme.txt", b"hello, archive\n", zipfile.ZIP_STORED)
    member(zf, "docs/", b"", zipfile.ZIP_STORED)
    member(zf, "docs/notes.txt", text(5000, 11))
    member(zf, "docs/empty.txt", b"")
    member(zf, "docs/noise.bin", noise(700, 12), level=9)
    member(zf, "café/日本.txt", "unicode names are UTF-8\n".encode())


class Unseekable(io.RawIOBase):
    def __init__(self):
        self.buf = bytearray()

    def writable(self):
        return True

    def write(self, b):
        self.buf += b
        return len(b)


def streamed():
    sink = Unseekable()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as zf:
        with zf.open(zipfile.ZipInfo("stream/a.txt", date_time=(2026, 1, 2, 3, 4, 6)), "w") as f:
            f.write(text(3000, 13))
        with zf.open(zipfile.ZipInfo("stream/b.txt", date_time=(2026, 1, 2, 3, 4, 6)), "w") as f:
            f.write(b"second")
    return bytes(sink.buf)


def forced_zip64():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        with zf.open(zipfile.ZipInfo("big.txt", date_time=(2026, 9, 29, 0, 0, 0)), "w", force_zip64=True) as f:
            f.write(text(2000, 14))
        member(zf, "small.txt", b"small")
    return buf.getvalue()


def with_zip64_end(archive):
    """Rewrite a plain archive's end record as ZIP64: a zip64 end record
    and locator before an end record whose fields are all 0xFFFF…"""
    eocd = archive.rfind(b"PK\x05\x06")
    sig, disk, cddisk, n1, n, size, offset, clen = struct.unpack("<4s4H2LH", archive[eocd:eocd + 22])
    body = archive[:eocd]
    z64 = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, n1, n, size, offset)
    loc = struct.pack("<4sLQL", b"PK\x06\x07", 0, len(body), 1)
    end = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    return body + z64 + loc + end


def tool_archives():
    out = []
    d = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(d, "folder", "sub"))
        open(os.path.join(d, "folder", "a.txt"), "wb").write(b"from Info-ZIP\n")
        open(os.path.join(d, "folder", "sub", "b.txt"), "wb").write(text(1500, 15))
        for path in ("folder", "folder/a.txt", "folder/sub", "folder/sub/b.txt"):
            os.utime(os.path.join(d, path), (1790000000, 1790000000))
        subprocess.run(["zip", "-q", "-r", "infozip.zip", "folder"], cwd=d, check=True)
        out.append(("infozip", open(os.path.join(d, "infozip.zip"), "rb").read()))
        p = subprocess.run(["zip", "-q", "-", "-"], cwd=d, input=text(1200, 16), capture_output=True, check=True)
        out.append(("infozip_stdin_streamed", p.stdout))
        subprocess.run(["ditto", "-c", "-k", "--keepParent", "folder", "ditto.zip"], cwd=d, check=True)
        out.append(("ditto", open(os.path.join(d, "ditto.zip"), "rb").read()))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return out


class Raw:
    """A zip written field by field, so any field can be wrong."""

    def __init__(self):
        self.body = bytearray()
        self.central = bytearray()
        self.count = 0

    def add(self, name, data, method=0, flags=0, crc=None, csize=None, usize=None, cd_name=None,
            extra=b"", cd_extra=None, version=20, comment=b"", local_flags=None, offset=None,
            local_sig=b"PK\x03\x04", payload=None):
        stored = data if payload is None else payload
        if method == 8 and payload is None:
            c = zlib.compressobj(6, zlib.DEFLATED, -15)
            stored = c.compress(data) + c.flush()
        crc = zlib.crc32(data) if crc is None else crc
        csize = len(stored) if csize is None else csize
        usize = len(data) if usize is None else usize
        cd_name = name if cd_name is None else cd_name
        cd_extra = extra if cd_extra is None else cd_extra
        lf = flags if local_flags is None else local_flags
        at = len(self.body) if offset is None else offset
        self.body += struct.pack("<4s5H3L2H", local_sig, version, lf, method, 0x6000, 0x5b3d,
                                 crc, csize, usize, len(name), len(extra)) + name + extra + stored
        self.central += struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 0x031e, version, flags, method, 0x6000, 0x5b3d,
                                    crc, csize, usize, len(cd_name), len(cd_extra), len(comment), 0, 0, 0, at)
        self.central += cd_name + cd_extra + comment
        self.count += 1
        return self

    def bytes(self, comment=b"", cd_offset=None, cd_size=None, count=None, prefix=b""):
        off = len(self.body) if cd_offset is None else cd_offset
        size = len(self.central) if cd_size is None else cd_size
        n = self.count if count is None else count
        end = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, n, n, size, off, len(comment)) + comment
        return prefix + bytes(self.body) + bytes(self.central) + end


def unicode_path_extra(original, name):
    data = struct.pack("<BL", 1, zlib.crc32(original)) + name.encode()
    return struct.pack("<HH", 0x7075, len(data)) + data


def archives():
    good = zipfile_bytes(basic)
    items = [
        ("zipfile_basic", good),
        ("zipfile_comments", zipfile_bytes(lambda zf: (member(zf, "a.txt", b"A", comment=b"member comment"),
                                                       setattr(zf, "comment", b"archive comment")))),
        ("zipfile_duplicates", zipfile_bytes(lambda zf: (member(zf, "same.txt", b"first"),
                                                         member(zf, "other.txt", b"other"),
                                                         member(zf, "same.txt", b"second")))),
        ("zipfile_streamed", streamed()),
        ("zipfile_prepended", b"#!/bin/sh\necho self-extracting stub\nexit 0\n" + good),
        ("zipfile_forced_zip64", forced_zip64()),
        ("zipfile_zip64_end", with_zip64_end(good)),
        ("zipfile_empty", zipfile_bytes(lambda zf: None)),
    ]
    items += tool_archives()

    cp = "café über ░.txt"
    items.append(("cp437_name", Raw().add(cp.encode("cp437"), b"cp437").bytes()))
    items.append(("unicode_path_extra", Raw().add(b"plain.txt", b"x", extra=unicode_path_extra(b"plain.txt", "résumé.txt")).bytes()))
    items.append(("unicode_path_extra_stale_crc", Raw().add(b"plain.txt", b"x", extra=unicode_path_extra(b"other.txt", "résumé.txt")).bytes()))
    items.append(("nul_in_name", Raw().add(b"visible.txt\x00.exe", b"nul").bytes()))
    items.append(("archive_comment_raw", Raw().add(b"a.txt", b"a").bytes(comment=b"PK trailing comment")))
    items.append(("extra_fields_skipped", Raw().add(b"a.txt", b"abc", method=8, extra=struct.pack("<HH", 0x5455, 5) + b"\x01\x00\x00\x00\x00").bytes()))

    # Malformed.
    items.append(("not_a_zip", b"this is not an archive at all" * 3))
    items.append(("empty_file", b""))
    items.append(("end_record_cut", good[:-5]))
    items.append(("bad_central_offset", Raw().add(b"a.txt", b"a").bytes(cd_offset=1000)))
    items.append(("negative_central_offset", Raw().add(b"a.txt", b"a").bytes(cd_offset=5000, cd_size=5000)))
    items.append(("central_truncated", Raw().add(b"a.txt", b"a").bytes(cd_size=80)))
    raw = bytearray(Raw().add(b"a.txt", b"a").bytes())
    cd = raw.find(b"PK\x01\x02")
    raw[cd:cd + 4] = b"PK\x01\x09"
    items.append(("central_bad_magic", bytes(raw)))
    items.append(("version_too_new", Raw().add(b"a.txt", b"a", version=64).bytes()))
    items.append(("corrupt_extra_field", Raw().add(b"a.txt", b"a", extra=struct.pack("<HH", 0x1234, 40)).bytes()))
    items.append(("utf8_flag_bad_bytes", Raw().add(b"ab\xffcd.txt", b"a", flags=0x800).bytes()))
    items.append(("utf8_flag_bad_continuation", Raw().add(b"ab\xe2\x28\xa1.txt", b"a", flags=0x800).bytes()))
    items.append(("utf8_flag_cut_sequence", Raw().add(b"ab\xe2\x82", b"a", flags=0x800).bytes()))
    items.append(("local_bad_magic", Raw().add(b"a.txt", b"a", local_sig=b"PK\x03\x09").bytes()))
    items.append(("local_name_differs", Raw().add(b"a.txt", b"a", cd_name=b"b.txt").bytes()))
    items.append(("local_header_bad_offset", Raw().add(b"a.txt", b"a", offset=40).bytes()))
    items.append(("local_header_truncated", Raw().add(b"a.txt", b"a", offset=1000000).bytes()))
    items.append(("overlapped_entries", Raw().add(b"a.txt", b"aaaa", csize=500).add(b"b.txt", b"b").bytes()))
    items.append(("shared_header_offset", Raw().add(b"a.txt", b"shared").bytes()[:0] or _shared_offset()))
    items.append(("encrypted_flag", Raw().add(b"secret.txt", b"s", flags=1).bytes()))
    items.append(("patched_data_flag", Raw().add(b"a.txt", b"a", flags=0x20).bytes()))
    items.append(("strong_encryption_flag", Raw().add(b"a.txt", b"a", flags=0x40).bytes()))
    items.append(("method_implode", Raw().add(b"a.txt", b"a", method=6).bytes()))
    items.append(("method_unknown", Raw().add(b"a.txt", b"a", method=99).bytes()))
    items.append(("bad_crc", Raw().add(b"a.txt", b"abc", crc=12345).bytes()))
    items.append(("bad_crc_deflated", Raw().add(b"a.txt", b"abcabcabc", method=8, crc=1).bytes()))
    d = text(800, 17)
    c = zlib.compressobj(6, zlib.DEFLATED, -15)
    full = c.compress(d) + c.flush()
    items.append(("deflate_truncated_in_archive", Raw().add(b"a.txt", d, method=8, payload=full[:len(full) // 2]).bytes()))
    items.append(("deflate_data_error_in_archive", Raw().add(b"a.txt", b"abc", method=8, payload=b"\xff\xff\xff").bytes()))
    items.append(("deflate_trailing_bytes", Raw().add(b"a.txt", d, method=8, payload=full + b"trailing").bytes()))
    items.append(("stored_longer_than_size", Raw().add(b"a.txt", b"abcdef", usize=3, crc=zlib.crc32(b"abc")).bytes()))
    items.append(("stored_shorter_than_size", Raw().add(b"a.txt", b"abc", usize=10).bytes()))
    items.append(("deflated_size_smaller", Raw().add(b"a.txt", d, method=8, usize=100, crc=zlib.crc32(d[:100])).bytes()))
    items.append(("zero_size_with_data", Raw().add(b"a.txt", b"abc", usize=0, crc=0).bytes()))
    return items


def _shared_offset():
    """Two directory entries naming one local header: zipfile warns, and
    reads both."""
    r = Raw().add(b"a.txt", b"shared")
    r.central += bytes(r.central)
    r.count += 1
    return r.bytes()


def err(e):
    return {"type": type(e).__name__, "message": str(e)}


def fields(info):
    return {
        "filename": info.filename,
        "orig_filename": info.orig_filename,
        "compress_type": info.compress_type,
        "flag_bits": info.flag_bits,
        "crc": info.CRC,
        "compress_size": info.compress_size,
        "file_size": info.file_size,
        "header_offset": info.header_offset,
        "date_time": list(info.date_time),
        "comment": info.comment.hex(),
        "extra_len": len(info.extra),
        "is_dir": info.is_dir(),
    }


# Opened from a file on disk, as the backend opens uploads, so that seek
# errors are the ones a real file raises.
scratch = tempfile.mkdtemp()
zips = []
for name, blob in archives():
    rec = {"name": name, "bytes": blob.hex()}
    path = os.path.join(scratch, name + ".zip")
    open(path, "wb").write(blob)
    try:
        zf = zipfile.ZipFile(path)
    except Exception as e:
        rec.update(ok=False, error=err(e))
        zips.append(rec)
        print(name, "open:", rec["error"])
        continue
    rec.update(ok=True, comment=zf.comment.hex(), entries=[fields(i) for i in zf.infolist()])
    reads = []
    names = []
    for i in zf.infolist():
        if i.filename not in names:
            names.append(i.filename)
    for n in names + ["no/such/member.txt"]:
        try:
            data = zf.read(n)
            reads.append({"name": n, "ok": True, "length": len(data), "crc": zlib.crc32(data),
                          "data": data.hex() if len(data) <= 256 else ""})
        except Exception as e:
            reads.append({"name": n, "ok": False, "error": err(e)})
    rec["reads"] = reads
    zips.append(rec)
    print(name, len(rec["entries"]), "entries;", [r.get("error", {}).get("type", r.get("length")) for r in reads])

shutil.rmtree(scratch, ignore_errors=True)
json.dump({"zlib": zlib.ZLIB_RUNTIME_VERSION, "python": sys.version.split()[0], "inflate": out, "zip": zips},
          open(os.path.join(here, "zip_oracle.json"), "w"), indent=1)
print(len(zips), "archives")
