# random -- CPython-compatible Mersenne Twister (MT19937) + distributions.
#
# Pure-TPy port of the upstream MT19937 reference (Matsumoto & Nishimura)
# and CPython's `_randommodule.c` seeding + CPython's `random.Random`
# distribution methods. For a non-negative int32 seed that fits in one
# uint32 word, getrandbits(32) / random() return byte-identical output to
# CPython after the same seed. Distributions (gauss, gammavariate,
# normalvariate, etc.) are straight ports of CPython's Lib/random.py so the
# per-seed output stream matches CPython exactly -- the cpython-phase test
# in cases/stdlib/random verifies this.
#
# Architecture:
#   - Random: class holding the 624-word MT state vector plus cached
#     second value from gauss() / normalvariate(). Per-instance RNGs and
#     all distribution methods live here.
#   - Module-level `_inst: Random` singleton, mutated in place by `seed()`.
#     Module-level `random()`, `randint()`, ... delegate to `_inst`.
#
# Thread safety: the module-level functions share `_inst` and are NOT
# thread-safe. Concurrent calls from multiple threads can corrupt the
# 624-word state (double-twist, interleaved index updates, torn reads).
# This is currently theoretical because TPy has no threading primitives
# -- when `threading` lands we'll need to pick a model (thread-local
# _inst / lock inside Random / deprecate module-level in favour of
# explicit instances) and revisit. CPython punts the same way: its docs
# tell users to use per-thread `Random()` instances, and free-threaded
# CPython (PEP 703, 3.13+) added internal locking to the C module.
# Until then, multi-threaded code should construct its own `Random(seed)`
# per thread rather than calling `random.random()` directly.
#
# TODO -- Tier 2 (should work, haven't pressure-tested):
#   - `getstate()` / `setstate(state)`: TPy can't easily synthesize a
#     625-tuple to match CPython's state type. A `list[uint32]`-based
#     shape works but is not CPython-compat.
#
# TODO -- Tier 3 (genuinely blocked on language work or missing
# primitives):
#   - `choices(pop, weights=, cum_weights=, k=)`: mechanical sigs +
#     weighted-selection wiring. The list-literal-vs-Iterable[T]
#     conformance gap that used to block this is now resolved.
#   - `sample(pop, k, counts=None)`: more involved algorithm (reservoir /
#     Floyd's) + same signature shape.
#   - `SystemRandom`: needs class-hierarchy decisions (subclass Random
#     with overrides, or standalone class). The OS entropy primitive
#     itself is wired (see `_os_entropy_uint32`).
#   - `seed(int)` for BigInt / `seed(bytes)` / `seed(str)`: need BigInt
#     word-iteration for the key array and/or bytes hashing.
#   - `binomialvariate(n, p)`: BTRS algorithm (Hormann 1993) is a
#     multi-case state machine and needs gauss()-driven path; defer until
#     demand surfaces.
#
# User-visible caveats (limitations of the currently-shipped API):
#   - `seed(n)` takes any int32 (negatives are mapped to abs()); BigInt /
#     bytes / str seeds are not accepted (see Tier 3 above).
#   - `randint(a, b)` requires `b - a + 1` to fit in int32. `randint(0,
#     INT32_MAX)` panics on the width computation. Use explicit `Random`
#     instances with custom code for wider ranges today.
#   - `randbytes(n)` produces byte-identical output to CPython's
#     `getrandbits(n*8).to_bytes(n, 'little')` for all n on any host,
#     including `n % 4 != 0` (partial-word packing uses the TOP bytes of
#     the final uint32, per CPython's getrandbits packing).
#   - `getrandbits(0)` raises `ValueError`. CPython returns 0 for k=0;
#     supporting it would require special-casing the BigInt zero return
#     and is rarely used. Match CPython if a real workload needs it.
#   - `choice([])` raises `IndexError`, matching CPython.
#
# Uses `uint32.add_wrap / sub_wrap / mul_wrap / shl_wrap` for the modular
# arithmetic MT needs (TPy's +/-/*/<< on fixed-width ints are
# overflow-checked).
#
# Performance notes (bench: tpyc -xO, x86_64, ccache):
#   Raw engine (genrand_uint32):   ~2 ns/call  (hand-C++ same algo: 1.2 ns)
#   random() (genrand_res53):      ~7 ns/call  (hand-C++ same algo: 3 ns)
#   randint(a, b):                 ~10 ns/call
#   Comparison: CPython's random() is ~54 ns/call; std::mt19937 (different
#   distribution algo) is ~3 ns/call for `uniform_real_distribution`.
#
# The ~0.8-4 ns gap vs same-algorithm hand-C++ is pure TPy safety overhead
# in the hot path, not codegen style or algorithm. Disassembly of
# _genrand_uint32 shows four sources, each individually tiny but additive:
#   - `__getitem__` bounds check on `_state[_index]` (`ja` branch +
#     `cmovs` for the negative-index normalize). `_index` is provably in
#     [0, N) from the `if _index >= N: generate()` guard, but the
#     optimizer doesn't see through it.
#   - `add_check<int32_t>` overflow check on `_index += 1` (`jo` branch).
#     `_index` is provably <= N = 624, never overflows.
#   - `-fstack-protector` canary (fs:0x28 load + compare on exit).
#     Build-config overhead, not random-specific.
#
# Possible future optimizations when/if the language supports them (none
# of these are worth adding ad-hoc workarounds for today -- the module is
# already ~7x faster than CPython, diminishing returns from here):
#   - `@unchecked` / `@noalloc` function attribute that suppresses
#     add_check/sub_check/mul_check and list bounds checks inside the
#     body. Would close most of the gap in one pass.
#   - `tpy.unsafe.get_unchecked(arr, i)` for bounds-free subscript at
#     specific sites. More surgical than @unchecked.
#   - Sema value-range analysis proving `_index in [0, 624)` so the
#     bounds check in `__getitem__` folds away. Already scaffolded for
#     other uses (tpyc/sema/value_range.py); would need plumbing to
#     codegen's __getitem__ lowering.
#   - `-fno-stack-protector` on release / noalloc builds (compiler-flag
#     decision, affects all code).
# tpy: cpp_namespace("tpystd::random")
# tpy: include("<tpy/stdlib/random.hpp>")
from tpy import int32, uint8, uint32, Array, copy, dispatch
from tpy.extern import native
import math


@native("tpy::stdlib::random::os_entropy_uint32")
def _os_entropy_uint32() -> uint32: ...

_N: int32 = 624
_M: int32 = 397
_MATRIX_A: uint32 = 0x9908b0df
_UPPER_MASK: uint32 = 0x80000000
_LOWER_MASK: uint32 = 0x7fffffff

# Constants for distributions, mirroring CPython's Lib/random.py.
_TWOPI: float = 2.0 * math.pi
_LOG4: float = math.log(4.0)
_SG_MAGICCONST: float = 1.0 + math.log(4.5)
_NV_MAGICCONST: float = 4.0 * math.exp(-0.5) / math.sqrt(2.0)

class Random:
    # Inline fixed-size state (std::array<uint32_t, 624>). Stack-allocated,
    # compile-time size enables bounds-check elision in __getitem__, and
    # loop unrolling / auto-vectorization in the twist. Measured ~3x
    # speedup vs list[uint32] on MT hot path.
    _state: Array[uint32, 624]
    _index: int32
    # Cached second value from gauss()'s Box-Muller pair. Matches CPython's
    # self.gauss_next using a bool flag instead of float|None sentinel.
    _gauss_next: float
    _has_gauss_next: bool

    def __init__(self, seed_value: uint32 | None = None) -> None:
        # std::array<uint32_t, 624>{} zero-inits every slot.
        self._state = Array[uint32, 624]()
        self._index = _N
        self._gauss_next = 0.0
        self._has_gauss_next = False
        if seed_value is None:
            self._seed(_os_entropy_uint32())
        else:
            self._seed(seed_value)

    def _init_genrand(self, s: uint32) -> None:
        self._state[0] = s
        mti: int32 = 1
        while mti < _N:
            prev: uint32 = self._state[mti - 1]
            self._state[mti] = uint32.add_wrap(
                uint32.mul_wrap(1812433253, prev ^ (prev >> 30)),
                uint32(mti),
            )
            mti += 1
        self._index = _N

    def _seed(self, s: uint32) -> None:
        # CPython's init_by_array specialised for a single-uint32 key [s].
        self._init_genrand(19650218)
        key0: uint32 = s
        key_length: int32 = 1
        i: int32 = 1
        j: int32 = 0
        k: int32 = _N
        if k < key_length:
            k = key_length
        while k > 0:
            prev: uint32 = self._state[i - 1]
            mixed: uint32 = uint32.mul_wrap(prev ^ (prev >> 30), 1664525)
            self._state[i] = uint32.add_wrap(
                uint32.add_wrap(self._state[i] ^ mixed, key0),
                uint32(j),
            )
            i += 1
            j += 1
            if i >= _N:
                self._state[0] = self._state[_N - 1]
                i = 1
            if j >= key_length:
                j = 0
            k -= 1
        k = _N - 1
        while k > 0:
            prev2: uint32 = self._state[i - 1]
            mixed2: uint32 = uint32.mul_wrap(prev2 ^ (prev2 >> 30), 1566083941)
            self._state[i] = uint32.sub_wrap(self._state[i] ^ mixed2, uint32(i))
            i += 1
            if i >= _N:
                self._state[0] = self._state[_N - 1]
                i = 1
            k -= 1
        # MSB=1 ensures non-zero initial state.
        self._state[0] = 0x80000000
        self._index = _N
        # Reset cached gauss value on reseed -- matches CPython's
        # Random.seed() setting self.gauss_next = None.
        self._has_gauss_next = False

    def _generate(self) -> None:
        kk: int32 = 0
        while kk < _N - _M:
            y: uint32 = (self._state[kk] & _UPPER_MASK) | (self._state[kk + 1] & _LOWER_MASK)
            mag: uint32 = 0
            if (y & 1) != 0:
                mag = _MATRIX_A
            self._state[kk] = self._state[kk + _M] ^ (y >> 1) ^ mag
            kk += 1
        while kk < _N - 1:
            y2: uint32 = (self._state[kk] & _UPPER_MASK) | (self._state[kk + 1] & _LOWER_MASK)
            mag2: uint32 = 0
            if (y2 & 1) != 0:
                mag2 = _MATRIX_A
            self._state[kk] = self._state[kk + _M - _N] ^ (y2 >> 1) ^ mag2
            kk += 1
        y3: uint32 = (self._state[_N - 1] & _UPPER_MASK) | (self._state[0] & _LOWER_MASK)
        mag3: uint32 = 0
        if (y3 & 1) != 0:
            mag3 = _MATRIX_A
        self._state[_N - 1] = self._state[_M - 1] ^ (y3 >> 1) ^ mag3
        self._index = 0

    def _genrand_uint32(self) -> uint32:
        if self._index >= _N:
            self._generate()
        y: uint32 = self._state[self._index]
        self._index += 1
        # Tempering.
        y = y ^ (y >> 11)
        y = y ^ (uint32.shl_wrap(y, 7) & 0x9d2c5680)
        y = y ^ (uint32.shl_wrap(y, 15) & 0xefc60000)
        y = y ^ (y >> 18)
        return y

    def random(self) -> float:
        # genrand_res53 -- 53-bit uniform in [0, 1), matches CPython.
        a: uint32 = self._genrand_uint32() >> 5   # top 27 bits
        b: uint32 = self._genrand_uint32() >> 6   # top 26 bits
        return (float(a) * 67108864.0 + float(b)) * (1.0 / 9007199254740992.0)

    def _genrand_top_bits(self, k: int32) -> uint32:
        # k in [1, 32]. Avoids the BigInt promotion that public
        # getrandbits does, so _randbelow's rejection loop stays uint32.
        return self._genrand_uint32() >> uint32(32 - k)

    def getrandbits(self, k: int32) -> int:
        # CPython-compatible: returns an int (BigInt) of k random bits.
        # k in [1, 32] uses one MT word; k > 32 concatenates ceil(k/32)
        # words little-endian (word[0] = low 32 bits), matching CPython.
        if k <= 0:
            raise ValueError("number of bits must be greater than zero")
        if k <= 32:
            return int(self._genrand_top_bits(k))
        numwords: int32 = (k + 31) // 32
        result: int = int(0)
        i: int32 = 0
        while i < numwords - 1:
            result = result | (int(self._genrand_uint32()) << (32 * i))
            i += 1
        last_k: int32 = k - 32 * i
        last_word: uint32 = self._genrand_uint32() >> uint32(32 - last_k)
        result = result | (int(last_word) << (32 * i))
        return result

    # ---------- Integer helpers ----------

    def _randbelow(self, n: uint32) -> uint32:
        # Uniform int in [0, n) via rejection sampling over a top-bits
        # slice where k = bit_length(n). Matches CPython's
        # _randbelow_with_getrandbits. Using bit_length(n-1) would agree
        # for non-power-of-2 widths but draws fewer bits per call for
        # powers of 2, diverging the MT stream from CPython.
        if n == 0:
            raise ValueError("_randbelow requires positive n")
        if n == 1:
            return 0
        m: uint32 = n
        k: int32 = 0
        while m > 0:
            k += 1
            m = m >> 1
        r: uint32 = self._genrand_top_bits(k)
        while r >= n:
            r = self._genrand_top_bits(k)
        return r

    def randint(self, a: int32, b: int32) -> int32:
        # Inclusive [a, b]. v1 constraint: b - a + 1 must fit in int32
        # (i.e. b - a <= INT32_MAX - 1). CPython handles arbitrary ints.
        if b < a:
            raise ValueError("empty range for randint()")
        width: int32 = b - a + 1
        return a + int32(self._randbelow(uint32(width)))

    @dispatch
    def randrange(self, stop: int32) -> int32:
        if stop <= 0:
            raise ValueError("empty range for randrange()")
        return int32(self._randbelow(uint32(stop)))

    @dispatch
    def randrange(self, start: int32, stop: int32) -> int32:
        if stop <= start:
            raise ValueError("empty range for randrange()")
        return start + int32(self._randbelow(uint32(stop - start)))

    @dispatch
    def randrange(self, start: int32, stop: int32, step: int32) -> int32:
        if step == 0:
            raise ValueError("zero step for randrange()")
        width: int32 = 0
        if step > 0:
            if stop <= start:
                raise ValueError("empty range for randrange()")
            # ceil((stop - start) / step)
            width = (stop - start + step - 1) // step
        else:
            if stop >= start:
                raise ValueError("empty range for randrange()")
            neg_step: int32 = -step
            width = (start - stop + neg_step - 1) // neg_step
        return start + step * int32(self._randbelow(uint32(width)))

    def randbytes(self, n: int32) -> bytes:
        # Matches CPython's `self.getrandbits(n * 8).to_bytes(n, 'little')`.
        # Full uint32 chunks yield their 4 bytes low-to-high (little-endian).
        # The last partial word uses its TOP `rem*8` bits (CPython's
        # getrandbits shifts each final word right by `32 - remaining_k`
        # before packing), so the partial bytes come from
        # `(w >> (32 - rem*8 + k*8)) & 0xff` for k in [0, rem).
        if n < 0:
            raise ValueError("n must be non-negative")
        out: bytearray = bytearray()
        i: int32 = 0
        full: int32 = n // 4
        while i < full:
            v: uint32 = self._genrand_uint32()
            out.append(uint8(v & 0xFF))
            out.append(uint8((v >> 8) & 0xFF))
            out.append(uint8((v >> 16) & 0xFF))
            out.append(uint8((v >> 24) & 0xFF))
            i += 1
        rem: int32 = n - full * 4
        if rem > 0:
            w: uint32 = self._genrand_uint32()
            shift_base: int32 = 32 - rem * 8
            j: int32 = 0
            while j < rem:
                out.append(uint8((w >> uint32(shift_base + j * 8)) & 0xFF))
                j += 1
        return bytes(out)

    # ---------- Sequence helpers ----------

    def choice[T](self, seq: list[T]) -> T:
        n: int32 = int32(len(seq))
        if n == 0:
            raise IndexError("Cannot choose from an empty sequence")
        return copy(seq[int32(self._randbelow(uint32(n)))])

    def shuffle[T](self, seq: list[T]) -> None:
        # Fisher-Yates / Durstenfeld in place. Matches CPython's
        # random.shuffle MT call sequence (one _randbelow(i+1) per step,
        # i from len-1 down to 1).
        i: int32 = int32(len(seq)) - 1
        while i > 0:
            j: int32 = int32(self._randbelow(uint32(i + 1)))
            tmp: T = copy(seq[i])
            seq[i] = copy(seq[j])
            seq[j] = tmp
            i -= 1

    # ---------- Continuous distributions ----------
    # All straight ports of CPython's Lib/random.py methods. Byte-identical
    # output on the same seed because libm (cos/sin/log/exp/sqrt) is the
    # same underlying implementation under CPython and TPy on Linux.

    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * self.random()

    def triangular(self, low: float = 0.0, high: float = 1.0, mode: float | None = None) -> float:
        u: float = self.random()
        if high == low:
            return low
        c: float = 0.5
        if mode is not None:
            c = (mode - low) / (high - low)
        if u > c:
            u = 1.0 - u
            c = 1.0 - c
            # CPython swaps low/high here; equivalent rewrite:
            return high + (low - high) * math.sqrt(u * c)
        return low + (high - low) * math.sqrt(u * c)

    def gauss(self, mu: float, sigma: float) -> float:
        # Box-Muller. Matches CPython's self.gauss(), which caches the
        # second generated value for the next call.
        if self._has_gauss_next:
            z: float = self._gauss_next
            self._has_gauss_next = False
            return mu + z * sigma
        x2pi: float = self.random() * _TWOPI
        g2rad: float = math.sqrt(-2.0 * math.log(1.0 - self.random()))
        z2: float = math.cos(x2pi) * g2rad
        self._gauss_next = math.sin(x2pi) * g2rad
        self._has_gauss_next = True
        return mu + z2 * sigma

    def normalvariate(self, mu: float, sigma: float) -> float:
        # Kinderman-Monahan method, distinct from gauss(). Matches CPython.
        # Does NOT populate _gauss_next: each call consumes fresh MT words.
        # gauss() and normalvariate() produce uncorrelated streams even when
        # interleaved; _gauss_next is gauss-only state.
        while True:
            u1: float = self.random()
            u2: float = 1.0 - self.random()
            z: float = _NV_MAGICCONST * (u1 - 0.5) / u2
            zz: float = z * z / 4.0
            if zz <= -math.log(u2):
                return mu + z * sigma

    def lognormvariate(self, mu: float, sigma: float) -> float:
        return math.exp(self.normalvariate(mu, sigma))

    def expovariate(self, lambd: float) -> float:
        # 1.0 - random() is in (0, 1], so log is in (-inf, 0], result >= 0.
        return -math.log(1.0 - self.random()) / lambd

    def paretovariate(self, alpha: float) -> float:
        u: float = 1.0 - self.random()
        return u ** (-1.0 / alpha)

    def weibullvariate(self, alpha: float, beta: float) -> float:
        u: float = 1.0 - self.random()
        return alpha * (-math.log(u)) ** (1.0 / beta)

    def gammavariate(self, alpha: float, beta: float) -> float:
        # CPython's algorithm: Cheng (1977) for alpha > 1, exponential for
        # alpha == 1, Ahrens-Dieter for 0 < alpha < 1.
        if alpha <= 0.0 or beta <= 0.0:
            raise ValueError("gammavariate: alpha and beta must be > 0")
        if alpha > 1.0:
            ainv: float = math.sqrt(2.0 * alpha - 1.0)
            bbb: float = alpha - _LOG4
            ccc: float = alpha + ainv
            while True:
                u1: float = self.random()
                if u1 <= 1e-7 or u1 >= 0.9999999:
                    continue
                u2: float = 1.0 - self.random()
                v: float = math.log(u1 / (1.0 - u1)) / ainv
                x: float = alpha * math.exp(v)
                z: float = u1 * u1 * u2
                r: float = bbb + ccc * v - x
                if r + _SG_MAGICCONST - 4.5 * z >= 0.0 or r >= math.log(z):
                    return x * beta
        if alpha == 1.0:
            return -math.log(1.0 - self.random()) * beta
        # 0 < alpha < 1: Ahrens-Dieter rejection sampling.
        b: float = (math.e + alpha) / math.e
        while True:
            u: float = self.random()
            p: float = b * u
            x2: float = 0.0
            if p <= 1.0:
                x2 = p ** (1.0 / alpha)
            else:
                x2 = -math.log((b - p) / alpha)
            u1b: float = self.random()
            if p > 1.0:
                if u1b <= x2 ** (alpha - 1.0):
                    return x2 * beta
            else:
                if u1b <= math.exp(-x2):
                    return x2 * beta

    def betavariate(self, alpha: float, beta: float) -> float:
        y: float = self.gammavariate(alpha, 1.0)
        if y <= 0.0:
            return 0.0
        return y / (y + self.gammavariate(beta, 1.0))

    def vonmisesvariate(self, mu: float, kappa: float) -> float:
        if kappa <= 1e-6:
            return _TWOPI * self.random()
        s: float = 0.5 / kappa
        r: float = s + math.sqrt(1.0 + s * s)
        z: float = 0.0
        while True:
            u1: float = self.random()
            z = math.cos(math.pi * u1)
            d: float = z / (r + z)
            u2: float = self.random()
            if u2 < 1.0 - d * d or u2 <= (1.0 - d) * math.exp(d):
                break
        q: float = 1.0 / r
        f: float = (q + z) / (1.0 + q * z)
        u3: float = self.random()
        mu_mod: float = mu % _TWOPI
        theta: float = 0.0
        if u3 > 0.5:
            theta = mu_mod + math.acos(f)
        else:
            theta = mu_mod - math.acos(f)
        return theta % _TWOPI


_inst: Random = Random()

# ---------- Module-level convenience API ----------
# All delegate to the singleton _inst. CPython does the same.

def random() -> float:
    return _inst.random()

@dispatch
def seed() -> None:
    # No-arg form: re-seed from OS entropy (matches CPython's seed()
    # default behavior when called without arguments).
    _inst._seed(_os_entropy_uint32())

@dispatch
def seed(n: int32) -> None:
    # CPython treats negative seeds as their absolute value. For
    # INT32_MIN the mathematical abs doesn't fit in int32, but
    # `0 - n` in uint32 (mod 2^32) equals abs(n) for any negative n.
    if n < 0:
        _inst._seed(uint32.sub_wrap(uint32(0), uint32.trunc(n)))
    else:
        _inst._seed(uint32(n))

def getrandbits(k: int32) -> int:
    return _inst.getrandbits(k)

def randint(a: int32, b: int32) -> int32:
    return _inst.randint(a, b)

def choice[T](seq: list[T]) -> T:
    return _inst.choice(seq)

def shuffle[T](seq: list[T]) -> None:
    _inst.shuffle(seq)

@dispatch
def randrange(stop: int32) -> int32:
    return _inst.randrange(stop)

@dispatch
def randrange(start: int32, stop: int32) -> int32:
    return _inst.randrange(start, stop)

@dispatch
def randrange(start: int32, stop: int32, step: int32) -> int32:
    return _inst.randrange(start, stop, step)

def randbytes(n: int32) -> bytes:
    return _inst.randbytes(n)

def uniform(a: float, b: float) -> float:
    return _inst.uniform(a, b)

def triangular(low: float = 0.0, high: float = 1.0, mode: float | None = None) -> float:
    return _inst.triangular(low, high, mode)

def gauss(mu: float, sigma: float) -> float:
    return _inst.gauss(mu, sigma)

def normalvariate(mu: float, sigma: float) -> float:
    return _inst.normalvariate(mu, sigma)

def lognormvariate(mu: float, sigma: float) -> float:
    return _inst.lognormvariate(mu, sigma)

def expovariate(lambd: float) -> float:
    return _inst.expovariate(lambd)

def paretovariate(alpha: float) -> float:
    return _inst.paretovariate(alpha)

def weibullvariate(alpha: float, beta: float) -> float:
    return _inst.weibullvariate(alpha, beta)

def gammavariate(alpha: float, beta: float) -> float:
    return _inst.gammavariate(alpha, beta)

def betavariate(alpha: float, beta: float) -> float:
    return _inst.betavariate(alpha, beta)

def vonmisesvariate(mu: float, kappa: float) -> float:
    return _inst.vonmisesvariate(mu, kappa)
