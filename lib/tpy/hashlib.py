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
#   - MD5 (RFC 1321)        -- uint32, little-endian state, 128-bit digest
#   - SHA-1 (FIPS 180-4)    -- uint32, big-endian,          160-bit digest
#   - SHA-512 (FIPS 180-4)  -- uint64, big-endian,          512-bit digest
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
# Language / compiler gaps hit while porting SHA-256 (see BUGS.md for
# detail; none of them are hashlib-specific, but they shaped this file):
#   - Forward-ref string annotations (`-> "SHA256"`) fail parse; class is
#     defined before the factory to avoid them.
#
# Uses tpy.bits.rotr32 for rotation and UIntN.add_wrap for wrapping
# addition (TPy's +/<< on fixed-width ints are overflow-checked; hash
# algorithms need modular arithmetic).
# tpy: cpp_namespace("tpystd::hashlib")
from tpy import int32, uint8, uint32, uint64, Own
from tpy.bits import rotr32

# ---------- SHA-256 ----------

_SHA256_H0: list[uint32] = [
    uint32(0x6a09e667), uint32(0xbb67ae85), uint32(0x3c6ef372), uint32(0xa54ff53a),
    uint32(0x510e527f), uint32(0x9b05688c), uint32(0x1f83d9ab), uint32(0x5be0cd19),
]

_SHA256_K: list[uint32] = [
    uint32(0x428a2f98), uint32(0x71374491), uint32(0xb5c0fbcf), uint32(0xe9b5dba5),
    uint32(0x3956c25b), uint32(0x59f111f1), uint32(0x923f82a4), uint32(0xab1c5ed5),
    uint32(0xd807aa98), uint32(0x12835b01), uint32(0x243185be), uint32(0x550c7dc3),
    uint32(0x72be5d74), uint32(0x80deb1fe), uint32(0x9bdc06a7), uint32(0xc19bf174),
    uint32(0xe49b69c1), uint32(0xefbe4786), uint32(0x0fc19dc6), uint32(0x240ca1cc),
    uint32(0x2de92c6f), uint32(0x4a7484aa), uint32(0x5cb0a9dc), uint32(0x76f988da),
    uint32(0x983e5152), uint32(0xa831c66d), uint32(0xb00327c8), uint32(0xbf597fc7),
    uint32(0xc6e00bf3), uint32(0xd5a79147), uint32(0x06ca6351), uint32(0x14292967),
    uint32(0x27b70a85), uint32(0x2e1b2138), uint32(0x4d2c6dfc), uint32(0x53380d13),
    uint32(0x650a7354), uint32(0x766a0abb), uint32(0x81c2c92e), uint32(0x92722c85),
    uint32(0xa2bfe8a1), uint32(0xa81a664b), uint32(0xc24b8b70), uint32(0xc76c51a3),
    uint32(0xd192e819), uint32(0xd6990624), uint32(0xf40e3585), uint32(0x106aa070),
    uint32(0x19a4c116), uint32(0x1e376c08), uint32(0x2748774c), uint32(0x34b0bcb5),
    uint32(0x391c0cb3), uint32(0x4ed8aa4a), uint32(0x5b9cca4f), uint32(0x682e6ff3),
    uint32(0x748f82ee), uint32(0x78a5636f), uint32(0x84c87814), uint32(0x8cc70208),
    uint32(0x90befffa), uint32(0xa4506ceb), uint32(0xbef9a3f7), uint32(0xc67178f2),
]

def _load_be32(data: bytes, off: int32) -> uint32:
    return (uint32(data[off]) << 24) | (uint32(data[off + 1]) << 16) | (uint32(data[off + 2]) << 8) | uint32(data[off + 3])

def _pack_be32(out: bytearray, v: uint32) -> None:
    out.append(uint8((v >> 24) & uint32(0xFF)))
    out.append(uint8((v >> 16) & uint32(0xFF)))
    out.append(uint8((v >> 8) & uint32(0xFF)))
    out.append(uint8(v & uint32(0xFF)))

class SHA256:
    h: list[uint32]
    buffer: bytearray
    length: uint64
    digest_size: int32
    block_size: int32
    name: str

    def __init__(self) -> None:
        self.h = []
        i: int32 = 0
        while i < 8:
            self.h.append(_SHA256_H0[i])
            i += 1
        self.buffer = bytearray()
        self.length = 0
        self.digest_size = 32
        self.block_size = 64
        self.name = "sha256"

    def update(self, data: bytes) -> None:
        n: int32 = int32(len(data))
        self.length = uint64.add_wrap(self.length, uint64(n))
        k: int32 = 0
        while k < n:
            self.buffer.append(data[k])
            k += 1
        self._drain_blocks()

    def _drain_blocks(self) -> None:
        while int32(len(self.buffer)) >= 64:
            self._process_block(bytes(self.buffer), 0)
            new_buf: bytearray = bytearray()
            j: int32 = 64
            total: int32 = int32(len(self.buffer))
            while j < total:
                new_buf.append(self.buffer[j])
                j += 1
            self.buffer = new_buf

    def _process_block(self, data: bytes, off: int32) -> None:
        w: list[uint32] = []
        i: int32 = 0
        while i < 16:
            w.append(_load_be32(data, off + 4 * i))
            i += 1
        while i < 64:
            w15: uint32 = w[i - 15]
            w2: uint32 = w[i - 2]
            s0: uint32 = rotr32(w15, 7) ^ rotr32(w15, 18) ^ (w15 >> 3)
            s1: uint32 = rotr32(w2, 17) ^ rotr32(w2, 19) ^ (w2 >> 10)
            w.append(uint32.add_wrap(uint32.add_wrap(uint32.add_wrap(w[i - 16], s0), w[i - 7]), s1))
            i += 1
        a: uint32 = self.h[0]
        b: uint32 = self.h[1]
        c: uint32 = self.h[2]
        d: uint32 = self.h[3]
        e: uint32 = self.h[4]
        f: uint32 = self.h[5]
        g: uint32 = self.h[6]
        hh: uint32 = self.h[7]
        t: int32 = 0
        while t < 64:
            bsig1: uint32 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25)
            ch: uint32 = (e & f) ^ ((~e) & g)
            t1: uint32 = uint32.add_wrap(uint32.add_wrap(uint32.add_wrap(uint32.add_wrap(hh, bsig1), ch), _SHA256_K[t]), w[t])
            bsig0: uint32 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22)
            maj: uint32 = (a & b) ^ (a & c) ^ (b & c)
            t2: uint32 = uint32.add_wrap(bsig0, maj)
            hh = g
            g = f
            f = e
            e = uint32.add_wrap(d, t1)
            d = c
            c = b
            b = a
            a = uint32.add_wrap(t1, t2)
            t += 1
        self.h[0] = uint32.add_wrap(self.h[0], a)
        self.h[1] = uint32.add_wrap(self.h[1], b)
        self.h[2] = uint32.add_wrap(self.h[2], c)
        self.h[3] = uint32.add_wrap(self.h[3], d)
        self.h[4] = uint32.add_wrap(self.h[4], e)
        self.h[5] = uint32.add_wrap(self.h[5], f)
        self.h[6] = uint32.add_wrap(self.h[6], g)
        self.h[7] = uint32.add_wrap(self.h[7], hh)

    def digest(self) -> bytes:
        clone: SHA256 = self.copy()
        bit_len: uint64 = uint64.add_wrap(clone.length, clone.length)
        bit_len = uint64.add_wrap(bit_len, bit_len)
        bit_len = uint64.add_wrap(bit_len, bit_len)  # x8 for bits
        clone.buffer.append(0x80)
        while int32(len(clone.buffer)) % 64 != 56:
            clone.buffer.append(0)
        i: int32 = 7
        while i >= 0:
            shift: uint64 = uint64(i * 8)
            clone.buffer.append(uint8((bit_len >> shift) & 0xFF))
            i -= 1
        clone._drain_blocks()
        out: bytearray = bytearray()
        i = 0
        while i < 8:
            _pack_be32(out, clone.h[i])
            i += 1
        return bytes(out)

    def hexdigest(self) -> str:
        return self.digest().hex()

    def copy(self) -> Own[SHA256]:
        c: SHA256 = SHA256()
        i: int32 = 0
        while i < 8:
            c.h[i] = self.h[i]
            i += 1
        c.length = self.length
        j: int32 = 0
        n: int32 = int32(len(self.buffer))
        while j < n:
            c.buffer.append(self.buffer[j])
            j += 1
        return c

def sha256(data: bytes = b"") -> Own[SHA256]:
    h: SHA256 = SHA256()
    h.update(data)
    return h
