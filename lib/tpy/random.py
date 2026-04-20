# random -- CPython-compatible Mersenne Twister (MT19937) + distributions.
#
# Pure-TPy port of the upstream MT19937 reference (Matsumoto & Nishimura)
# and CPython's `_randommodule.c` seeding + CPython's `random.Random`
# distribution methods. For a non-negative Int32 seed that fits in one
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
# TODO -- Tier 2 (should work, haven't pressure-tested; mostly require
# verifying a sub-feature and then tracing through):
#   - `choice(seq)`: straightforward for `list[T]` with value-type T; may
#     hit the `list.pop()` ref-type gap (STDLIB_ROADMAP cross-cutting
#     table, ref-type list ops) for user-class elements.
#   - `shuffle(seq)`: depends on whether `seq[i], seq[j] = seq[j], seq[i]`
#     lowers correctly for list elements (tuple-swap on subscripts); if
#     not, a temp var works.
#   - `getrandbits(k)` for k > 32: needs BigInt `<<`/`|` -- straightforward
#     if BigInt bit-ops are wired.
#   - `getstate()` / `setstate(state)`: TPy can't easily synthesize a
#     625-tuple to match CPython's state type. A `list[UInt32]`-based
#     shape works but is not CPython-compat.
#
# TODO -- Tier 3 (genuinely blocked on language work or missing
# primitives):
#   - `choices(pop, weights=, cum_weights=, k=)`: soft block on list-literal
#     conformance to `Iterable[float]` for `weights=` (TODO.md:53). Ship
#     today as `list[float]` and document the CPython-compat regression
#     when we do.
#   - `sample(pop, k, counts=None)`: same `Iterable[T]` gap, plus a more
#     involved algorithm.
#   - `seed(None)` implicit auto-seed / `SystemRandom`: both need an OS
#     entropy primitive (~10-line `@native` to `getentropy(3)` /
#     `std::random_device`). Independent of the `os` module.
#   - `seed(int)` for BigInt / `seed(bytes)` / `seed(str)`: need BigInt
#     word-iteration for the key array and/or bytes hashing.
#   - `binomialvariate(n, p)`: BTRS algorithm (Hormann 1993) is a
#     multi-case state machine and needs gauss()-driven path; defer until
#     demand surfaces.
#
# User-visible caveats (limitations of the currently-shipped API):
#   - `seed(n)` takes a non-negative Int32 only. Negative values panic on
#     the Int32->UInt32 coercion; BigInt/bytes/str are not accepted. See
#     Tier 3 above.
#   - `randint(a, b)` requires `b - a + 1` to fit in Int32. `randint(0,
#     INT32_MAX)` panics on the width computation. Use explicit `Random`
#     instances with custom code for wider ranges today.
#   - `randbytes(n)` produces byte-identical output to CPython's
#     `getrandbits(n*8).to_bytes(n, 'little')` for all n on any host,
#     including `n % 4 != 0` (partial-word packing uses the TOP bytes of
#     the final uint32, per CPython's getrandbits packing).
#   - `getrandbits(k)` rejects k > 32 with ValueError. See Tier 2 above.
#
# Pending simplifications (fold back in when the referenced language bug
# lands; keeping these documented so we don't forget the code is awkward
# on purpose):
#   - `vonmisesvariate` uses inline `x - floor(x/TWOPI) * TWOPI` instead of
#     Python's `%`, because TPy's `%` on floats is C's fmod (truncation)
#     semantics while CPython uses floor (TODO.md:56). When TODO.md:56 is
#     fixed, revert to the straightforward `% _TWOPI` form.
#   - `Random.__init__(seed_value: UInt32 = UInt32(5489))` inlines the
#     literal default because named-const defaults (`_DEFAULT_SEED`) are
#     rejected by sema (TODO.md:72). When TODO.md:72 is fixed, switch to
#     `seed_value: UInt32 = _DEFAULT_SEED` for readability.
#   - MT tempering uses `UInt32.mul_wrap(y, 1 << n)` instead of the direct
#     `y << n` because TPy's `<<` on UInt32 is overflow-checked. A
#     `UInt32.shl_wrap(y, n)` static method paralleling `add_wrap` /
#     `sub_wrap` / `mul_wrap` would let the code read as a shift (not a
#     multiply). Every bit-twiddling stdlib module that lands (re-usable
#     ciphers, RNG variants, hash follow-ups beyond SHA-256) will want
#     this same helper; consider adding it to `fixed_int.hpp` alongside
#     the existing wrap helpers.
#
# Uses `UInt32.add_wrap / sub_wrap / mul_wrap` for the modular arithmetic
# MT needs (TPy's +/-/* on fixed-width ints are overflow-checked). `<< n`
# on UInt32 is overflow-checked too, so the tempering step uses
# `mul_wrap(y, 1 << n)` instead of `y << n` (see pending-simplifications
# above).
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
from tpy import Int32, UInt8, UInt32, Array
from typing import overload
import math

_N: Int32 = 624
_M: Int32 = 397
_MATRIX_A: UInt32 = UInt32(0x9908b0df)
_UPPER_MASK: UInt32 = UInt32(0x80000000)
_LOWER_MASK: UInt32 = UInt32(0x7fffffff)

# Historical default MT seed. Used only before the user calls seed();
# a real auto-seed-from-entropy comes with the OS-entropy binding.
_DEFAULT_SEED: UInt32 = UInt32(5489)

# Constants for distributions, mirroring CPython's Lib/random.py.
_TWOPI: float = 2.0 * math.pi
_LOG4: float = math.log(4.0)
_SG_MAGICCONST: float = 1.0 + math.log(4.5)
_NV_MAGICCONST: float = 4.0 * math.exp(-0.5) / math.sqrt(2.0)

class Random:
    # Inline fixed-size state (std::array<uint32_t, 624>). Stack-allocated,
    # compile-time size enables bounds-check elision in __getitem__, and
    # loop unrolling / auto-vectorization in the twist. Measured ~3x
    # speedup vs list[UInt32] on MT hot path.
    _state: Array[UInt32, 624]
    _index: Int32
    # Cached second value from gauss()'s Box-Muller pair. Matches CPython's
    # self.gauss_next using a bool flag instead of float|None sentinel.
    _gauss_next: float
    _has_gauss_next: bool

    def __init__(self, seed_value: UInt32 = UInt32(5489)) -> None:
        # std::array<uint32_t, 624>{} zero-inits every slot.
        self._state = Array[UInt32, 624]()
        self._index = _N
        self._gauss_next = 0.0
        self._has_gauss_next = False
        self._seed(seed_value)

    def _init_genrand(self, s: UInt32) -> None:
        self._state[0] = s
        mti: Int32 = 1
        while mti < _N:
            prev: UInt32 = self._state[mti - 1]
            self._state[mti] = UInt32.add_wrap(
                UInt32.mul_wrap(UInt32(1812433253), prev ^ (prev >> UInt32(30))),
                UInt32(mti),
            )
            mti += 1
        self._index = _N

    def _seed(self, s: UInt32) -> None:
        # CPython's init_by_array specialised for a single-uint32 key [s].
        self._init_genrand(UInt32(19650218))
        key0: UInt32 = s
        key_length: Int32 = 1
        i: Int32 = 1
        j: Int32 = 0
        k: Int32 = _N
        if k < key_length:
            k = key_length
        while k > 0:
            prev: UInt32 = self._state[i - 1]
            mixed: UInt32 = UInt32.mul_wrap(prev ^ (prev >> UInt32(30)), UInt32(1664525))
            self._state[i] = UInt32.add_wrap(
                UInt32.add_wrap(self._state[i] ^ mixed, key0),
                UInt32(j),
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
            prev2: UInt32 = self._state[i - 1]
            mixed2: UInt32 = UInt32.mul_wrap(prev2 ^ (prev2 >> UInt32(30)), UInt32(1566083941))
            self._state[i] = UInt32.sub_wrap(self._state[i] ^ mixed2, UInt32(i))
            i += 1
            if i >= _N:
                self._state[0] = self._state[_N - 1]
                i = 1
            k -= 1
        # MSB=1 ensures non-zero initial state.
        self._state[0] = UInt32(0x80000000)
        self._index = _N
        # Reset cached gauss value on reseed -- matches CPython's
        # Random.seed() setting self.gauss_next = None.
        self._has_gauss_next = False

    def _generate(self) -> None:
        kk: Int32 = 0
        while kk < _N - _M:
            y: UInt32 = (self._state[kk] & _UPPER_MASK) | (self._state[kk + 1] & _LOWER_MASK)
            mag: UInt32 = UInt32(0)
            if (y & UInt32(1)) != UInt32(0):
                mag = _MATRIX_A
            self._state[kk] = self._state[kk + _M] ^ (y >> UInt32(1)) ^ mag
            kk += 1
        while kk < _N - 1:
            y2: UInt32 = (self._state[kk] & _UPPER_MASK) | (self._state[kk + 1] & _LOWER_MASK)
            mag2: UInt32 = UInt32(0)
            if (y2 & UInt32(1)) != UInt32(0):
                mag2 = _MATRIX_A
            self._state[kk] = self._state[kk + _M - _N] ^ (y2 >> UInt32(1)) ^ mag2
            kk += 1
        y3: UInt32 = (self._state[_N - 1] & _UPPER_MASK) | (self._state[0] & _LOWER_MASK)
        mag3: UInt32 = UInt32(0)
        if (y3 & UInt32(1)) != UInt32(0):
            mag3 = _MATRIX_A
        self._state[_N - 1] = self._state[_M - 1] ^ (y3 >> UInt32(1)) ^ mag3
        self._index = 0

    def _genrand_uint32(self) -> UInt32:
        if self._index >= _N:
            self._generate()
        y: UInt32 = self._state[self._index]
        self._index += 1
        # Tempering. `<<` on UInt32 is overflow-checked; use mul_wrap.
        y = y ^ (y >> UInt32(11))
        y = y ^ (UInt32.mul_wrap(y, UInt32(128)) & UInt32(0x9d2c5680))
        y = y ^ (UInt32.mul_wrap(y, UInt32(32768)) & UInt32(0xefc60000))
        y = y ^ (y >> UInt32(18))
        return y

    def random(self) -> float:
        # genrand_res53 -- 53-bit uniform in [0, 1), matches CPython.
        a: UInt32 = self._genrand_uint32() >> UInt32(5)   # top 27 bits
        b: UInt32 = self._genrand_uint32() >> UInt32(6)   # top 26 bits
        return (float(a) * 67108864.0 + float(b)) * (1.0 / 9007199254740992.0)

    def getrandbits(self, k: Int32) -> UInt32:
        # CPython supports arbitrary k via multi-word concatenation; this
        # v1 covers k in [1, 32] which is the common-case path (matches
        # CPython's byte-identical uint32 slice).
        if k <= 0 or k > 32:
            raise ValueError("number of bits must be in 1..32 (v1 limit)")
        return self._genrand_uint32() >> UInt32(32 - k)

    # ---------- Integer helpers ----------

    def _randbelow(self, n: UInt32) -> UInt32:
        # Uniform int in [0, n) via rejection sampling over getrandbits(k)
        # where k = bit_length(n). Matches CPython's
        # _randbelow_with_getrandbits. Using bit_length(n-1) would agree
        # for non-power-of-2 widths but draws fewer bits per call for
        # powers of 2, diverging the MT stream from CPython.
        if n == UInt32(0):
            raise ValueError("_randbelow requires positive n")
        if n == UInt32(1):
            return UInt32(0)
        m: UInt32 = n
        k: Int32 = 0
        while m > UInt32(0):
            k += 1
            m = m >> UInt32(1)
        r: UInt32 = self.getrandbits(k)
        while r >= n:
            r = self.getrandbits(k)
        return r

    def randint(self, a: Int32, b: Int32) -> Int32:
        # Inclusive [a, b]. v1 constraint: b - a + 1 must fit in Int32
        # (i.e. b - a <= INT32_MAX - 1). CPython handles arbitrary ints.
        if b < a:
            raise ValueError("empty range for randint()")
        width: Int32 = b - a + 1
        return a + Int32(self._randbelow(UInt32(width)))

    @overload
    def randrange(self, stop: Int32) -> Int32:
        if stop <= 0:
            raise ValueError("empty range for randrange()")
        return Int32(self._randbelow(UInt32(stop)))

    @overload
    def randrange(self, start: Int32, stop: Int32) -> Int32:
        if stop <= start:
            raise ValueError("empty range for randrange()")
        return start + Int32(self._randbelow(UInt32(stop - start)))

    @overload
    def randrange(self, start: Int32, stop: Int32, step: Int32) -> Int32:
        if step == 0:
            raise ValueError("zero step for randrange()")
        width: Int32 = 0
        if step > 0:
            if stop <= start:
                raise ValueError("empty range for randrange()")
            # ceil((stop - start) / step)
            width = (stop - start + step - 1) // step
        else:
            if stop >= start:
                raise ValueError("empty range for randrange()")
            neg_step: Int32 = -step
            width = (start - stop + neg_step - 1) // neg_step
        return start + step * Int32(self._randbelow(UInt32(width)))

    def randbytes(self, n: Int32) -> bytes:
        # Matches CPython's `self.getrandbits(n * 8).to_bytes(n, 'little')`.
        # Full uint32 chunks yield their 4 bytes low-to-high (little-endian).
        # The last partial word uses its TOP `rem*8` bits (CPython's
        # getrandbits shifts each final word right by `32 - remaining_k`
        # before packing), so the partial bytes come from
        # `(w >> (32 - rem*8 + k*8)) & 0xff` for k in [0, rem).
        if n < 0:
            raise ValueError("n must be non-negative")
        out: bytearray = bytearray()
        i: Int32 = 0
        full: Int32 = n // 4
        while i < full:
            v: UInt32 = self._genrand_uint32()
            out.append(UInt8(v & UInt32(0xFF)))
            out.append(UInt8((v >> UInt32(8)) & UInt32(0xFF)))
            out.append(UInt8((v >> UInt32(16)) & UInt32(0xFF)))
            out.append(UInt8((v >> UInt32(24)) & UInt32(0xFF)))
            i += 1
        rem: Int32 = n - full * 4
        if rem > 0:
            w: UInt32 = self._genrand_uint32()
            shift_base: Int32 = 32 - rem * 8
            j: Int32 = 0
            while j < rem:
                out.append(UInt8((w >> UInt32(shift_base + j * 8)) & UInt32(0xFF)))
                j += 1
        return bytes(out)

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
        # CPython uses Python's floor-semantics `%`; TPy's `%` on floats is
        # truncation (TODO.md:56). Inline floor-mod to match CPython output.
        mu_mod: float = mu - math.floor(mu / _TWOPI) * _TWOPI
        theta: float = 0.0
        if u3 > 0.5:
            theta = mu_mod + math.acos(f)
        else:
            theta = mu_mod - math.acos(f)
        return theta - math.floor(theta / _TWOPI) * _TWOPI


_inst: Random = Random(_DEFAULT_SEED)

# ---------- Module-level convenience API ----------
# All delegate to the singleton _inst. CPython does the same.

def random() -> float:
    return _inst.random()

def seed(n: Int32) -> None:
    # CPython accepts negative ints (treats as abs) and bigints; we accept
    # a non-negative Int32. Negative Int32 will panic on the UInt32 coercion.
    _inst._seed(UInt32(n))

def getrandbits(k: Int32) -> UInt32:
    return _inst.getrandbits(k)

def randint(a: Int32, b: Int32) -> Int32:
    return _inst.randint(a, b)

@overload
def randrange(stop: Int32) -> Int32:
    return _inst.randrange(stop)

@overload
def randrange(start: Int32, stop: Int32) -> Int32:
    return _inst.randrange(start, stop)

@overload
def randrange(start: Int32, stop: Int32, step: Int32) -> Int32:
    return _inst.randrange(start, stop, step)

def randbytes(n: Int32) -> bytes:
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
