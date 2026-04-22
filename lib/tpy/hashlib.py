# hashlib -- cryptographic hash algorithms.
#
# Only SHA-256 (FIPS 180-4) is shipped today. Pure-TPy straight-line port
# of the reference algorithm; no SIMD, no OpenSSL binding. Correctness
# first; an optional fast backend is a future follow-up (see
# STDLIB_ROADMAP).
#
# TODO -- algorithms to add (each follows the same class + factory
# pattern used by SHA256 below; mechanical copies differing only in
# round function, initial state, word width, and endianness):
#   - MD5 (RFC 1321)        -- UInt32, little-endian state, 128-bit digest
#   - SHA-1 (FIPS 180-4)    -- UInt32, big-endian,          160-bit digest
#   - SHA-512 (FIPS 180-4)  -- UInt64, big-endian,          512-bit digest
#   - BLAKE2b / BLAKE2s     -- separate family, larger surface
#   - SHA-3 / SHAKE         -- Keccak sponge, separate algorithm
#
# TODO -- module-level surface missing:
#   - `hashlib.new(name, data=None)` -- factory-by-name dispatcher.
#     Needs either a dict-of-factories or runtime type resolution.
#   - `algorithms_available` / `algorithms_guaranteed` -- frozenset[str].
#
# TODO -- perf:
#   - Bind OpenSSL / libcrypto as an optional fast backend, gated by the
#     F8 feature-flag system (see FEATURE_ROADMAP.md). Drop-in replacement
#     for the pure-TPy inner loop; pure-TPy stays as deps-free default.
#
# Language / compiler gaps hit while porting SHA-256 (see TODO.md for
# detail; none of them are hashlib-specific, but they shaped this file):
#   - Cross-module method lookup requires the class to be imported into
#     the caller. Users of this module must `from hashlib import sha256,
#     SHA256` (the class name) to call methods on the returned object.
#     Tracked in TODO.md ("Cross-module method lookup"). This also
#     forces `tests/cases/stdlib/hashlib/` to be marked no_cpython:
#     CPython's `hashlib` has no `SHA256` name, so the same import that
#     satisfies TPy fails under CPython. Once the compiler gap is fixed,
#     drop the `SHA256` import and delete the no_cpython.txt marker.
#   - Default parameter value `b""` rejected as non-constant; we use
#     `bytes | None = None` instead.
#   - Free function with a `bytearray` param + `.append()` inside gets
#     auto-inferred as const, producing "discards qualifiers" C++ errors.
#     Worked around by inlining `_pack_be32` into `digest()`.
#   - Forward-ref string annotations (`-> "SHA256"`) fail parse; class is
#     defined before the factory to avoid them.
#
# Uses tpy.bits.rotr32 for rotation and UIntN.add_wrap for wrapping
# addition (TPy's +/<< on fixed-width ints are overflow-checked; hash
# algorithms need modular arithmetic).
# tpy: cpp_namespace("tpystd::hashlib")
from tpy import Int32, UInt8, UInt32, UInt64, Own
from tpy.bits import rotr32

# ---------- SHA-256 ----------

_SHA256_H0: list[UInt32] = [
    UInt32(0x6a09e667), UInt32(0xbb67ae85), UInt32(0x3c6ef372), UInt32(0xa54ff53a),
    UInt32(0x510e527f), UInt32(0x9b05688c), UInt32(0x1f83d9ab), UInt32(0x5be0cd19),
]

_SHA256_K: list[UInt32] = [
    UInt32(0x428a2f98), UInt32(0x71374491), UInt32(0xb5c0fbcf), UInt32(0xe9b5dba5),
    UInt32(0x3956c25b), UInt32(0x59f111f1), UInt32(0x923f82a4), UInt32(0xab1c5ed5),
    UInt32(0xd807aa98), UInt32(0x12835b01), UInt32(0x243185be), UInt32(0x550c7dc3),
    UInt32(0x72be5d74), UInt32(0x80deb1fe), UInt32(0x9bdc06a7), UInt32(0xc19bf174),
    UInt32(0xe49b69c1), UInt32(0xefbe4786), UInt32(0x0fc19dc6), UInt32(0x240ca1cc),
    UInt32(0x2de92c6f), UInt32(0x4a7484aa), UInt32(0x5cb0a9dc), UInt32(0x76f988da),
    UInt32(0x983e5152), UInt32(0xa831c66d), UInt32(0xb00327c8), UInt32(0xbf597fc7),
    UInt32(0xc6e00bf3), UInt32(0xd5a79147), UInt32(0x06ca6351), UInt32(0x14292967),
    UInt32(0x27b70a85), UInt32(0x2e1b2138), UInt32(0x4d2c6dfc), UInt32(0x53380d13),
    UInt32(0x650a7354), UInt32(0x766a0abb), UInt32(0x81c2c92e), UInt32(0x92722c85),
    UInt32(0xa2bfe8a1), UInt32(0xa81a664b), UInt32(0xc24b8b70), UInt32(0xc76c51a3),
    UInt32(0xd192e819), UInt32(0xd6990624), UInt32(0xf40e3585), UInt32(0x106aa070),
    UInt32(0x19a4c116), UInt32(0x1e376c08), UInt32(0x2748774c), UInt32(0x34b0bcb5),
    UInt32(0x391c0cb3), UInt32(0x4ed8aa4a), UInt32(0x5b9cca4f), UInt32(0x682e6ff3),
    UInt32(0x748f82ee), UInt32(0x78a5636f), UInt32(0x84c87814), UInt32(0x8cc70208),
    UInt32(0x90befffa), UInt32(0xa4506ceb), UInt32(0xbef9a3f7), UInt32(0xc67178f2),
]

def _load_be32(data: bytes, off: Int32) -> UInt32:
    return (UInt32(data[off]) << 24) | (UInt32(data[off + 1]) << 16) | (UInt32(data[off + 2]) << 8) | UInt32(data[off + 3])

class SHA256:
    h: list[UInt32]
    buffer: bytearray
    length: UInt64
    digest_size: Int32
    block_size: Int32
    name: str

    def __init__(self) -> None:
        self.h = []
        i: Int32 = 0
        while i < 8:
            self.h.append(_SHA256_H0[i])
            i += 1
        self.buffer = bytearray()
        self.length = UInt64(0)
        self.digest_size = 32
        self.block_size = 64
        self.name = "sha256"

    def update(self, data: bytes) -> None:
        n: Int32 = Int32(len(data))
        self.length = UInt64.add_wrap(self.length, UInt64(n))
        k: Int32 = 0
        while k < n:
            self.buffer.append(data[k])
            k += 1
        self._drain_blocks()

    def _drain_blocks(self) -> None:
        while Int32(len(self.buffer)) >= 64:
            self._process_block(bytes(self.buffer), 0)
            new_buf: bytearray = bytearray()
            j: Int32 = 64
            total: Int32 = Int32(len(self.buffer))
            while j < total:
                new_buf.append(self.buffer[j])
                j += 1
            self.buffer = new_buf

    def _process_block(self, data: bytes, off: Int32) -> None:
        w: list[UInt32] = []
        i: Int32 = 0
        while i < 16:
            w.append(_load_be32(data, off + 4 * i))
            i += 1
        while i < 64:
            w15: UInt32 = w[i - 15]
            w2: UInt32 = w[i - 2]
            s0: UInt32 = rotr32(w15, 7) ^ rotr32(w15, 18) ^ (w15 >> UInt32(3))
            s1: UInt32 = rotr32(w2, 17) ^ rotr32(w2, 19) ^ (w2 >> UInt32(10))
            w.append(UInt32.add_wrap(UInt32.add_wrap(UInt32.add_wrap(w[i - 16], s0), w[i - 7]), s1))
            i += 1
        a: UInt32 = self.h[0]
        b: UInt32 = self.h[1]
        c: UInt32 = self.h[2]
        d: UInt32 = self.h[3]
        e: UInt32 = self.h[4]
        f: UInt32 = self.h[5]
        g: UInt32 = self.h[6]
        hh: UInt32 = self.h[7]
        t: Int32 = 0
        while t < 64:
            bsig1: UInt32 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25)
            ch: UInt32 = (e & f) ^ ((~e) & g)
            t1: UInt32 = UInt32.add_wrap(UInt32.add_wrap(UInt32.add_wrap(UInt32.add_wrap(hh, bsig1), ch), _SHA256_K[t]), w[t])
            bsig0: UInt32 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22)
            maj: UInt32 = (a & b) ^ (a & c) ^ (b & c)
            t2: UInt32 = UInt32.add_wrap(bsig0, maj)
            hh = g
            g = f
            f = e
            e = UInt32.add_wrap(d, t1)
            d = c
            c = b
            b = a
            a = UInt32.add_wrap(t1, t2)
            t += 1
        self.h[0] = UInt32.add_wrap(self.h[0], a)
        self.h[1] = UInt32.add_wrap(self.h[1], b)
        self.h[2] = UInt32.add_wrap(self.h[2], c)
        self.h[3] = UInt32.add_wrap(self.h[3], d)
        self.h[4] = UInt32.add_wrap(self.h[4], e)
        self.h[5] = UInt32.add_wrap(self.h[5], f)
        self.h[6] = UInt32.add_wrap(self.h[6], g)
        self.h[7] = UInt32.add_wrap(self.h[7], hh)

    def digest(self) -> bytes:
        clone: SHA256 = self.copy()
        bit_len: UInt64 = UInt64.add_wrap(clone.length, clone.length)
        bit_len = UInt64.add_wrap(bit_len, bit_len)
        bit_len = UInt64.add_wrap(bit_len, bit_len)  # x8 for bits
        clone.buffer.append(UInt8(0x80))
        while Int32(len(clone.buffer)) % 64 != 56:
            clone.buffer.append(UInt8(0))
        i: Int32 = 7
        while i >= 0:
            shift: UInt64 = UInt64(i * 8)
            clone.buffer.append(UInt8((bit_len >> shift) & UInt64(0xFF)))
            i -= 1
        clone._drain_blocks()
        out: bytearray = bytearray()
        i = 0
        while i < 8:
            v: UInt32 = clone.h[i]
            out.append(UInt8((v >> 24) & UInt32(0xFF)))
            out.append(UInt8((v >> 16) & UInt32(0xFF)))
            out.append(UInt8((v >> 8) & UInt32(0xFF)))
            out.append(UInt8(v & UInt32(0xFF)))
            i += 1
        return bytes(out)

    def hexdigest(self) -> str:
        return self.digest().hex()

    def copy(self) -> Own[SHA256]:
        c: SHA256 = SHA256()
        i: Int32 = 0
        while i < 8:
            c.h[i] = self.h[i]
            i += 1
        c.length = self.length
        j: Int32 = 0
        n: Int32 = Int32(len(self.buffer))
        while j < n:
            c.buffer.append(self.buffer[j])
            j += 1
        return c

def sha256(data: bytes | None = None) -> Own[SHA256]:
    h: SHA256 = SHA256()
    if data is not None:
        h.update(data)
    return h
