# random -- MT19937 engine byte-identity with CPython + Tier 1 surface.
# The engine's uint32 stream is the primary correctness check; distribution
# functions are verified byte-identical to CPython by printing a scaled
# integer representation (float-print in TPy diverges from CPython for
# |x| < 0.1 due to a pre-existing format_float precision bug).
import random
from random import Random
from tpy import Int32, UInt32


def _p(x: float) -> None:
    # Work around format_float's precision loss on |x| < 0.1.
    # int(x * 1e14) is a deterministic IEEE-754 op -- same bits on TPy
    # and CPython -- and the integer prints correctly on both.
    print(int(x * 1e14))


def main() -> None:
    # --- Engine: byte-identical uint32 stream vs CPython for seed=42.
    random.seed(Int32(42))
    print("engine_u32:")
    for _ in range(10):
        print(int(random._inst.getrandbits(32)))

    # Another seed, shorter run.
    random.seed(Int32(1))
    print("seed1_u32:")
    for _ in range(5):
        print(int(random._inst.getrandbits(32)))

    # --- Reproducibility of random() on the same seed.
    random.seed(Int32(42))
    a0: float = random.random()
    a1: float = random.random()
    random.seed(Int32(42))
    b0: float = random.random()
    b1: float = random.random()
    print("reproducible:", a0 == b0, a1 == b1)

    # --- Range of random().
    random.seed(Int32(7))
    i: Int32 = 0
    in_range: bool = True
    while i < 1000:
        v: float = random.random()
        if v < 0.0 or v >= 1.0:
            in_range = False
        i += 1
    print("in_range:", in_range)

    # --- Different seeds diverge.
    random.seed(Int32(1))
    s1: float = random.random()
    random.seed(Int32(2))
    s2: float = random.random()
    print("distinct_seeds:", s1 != s2)

    # --- Per-instance Random independence.
    r1: Random = Random(UInt32(42))
    r2: Random = Random(UInt32(42))
    print("instance_same_seed:", r1.random() == r2.random())

    r3: Random = Random(UInt32(42))
    r4: Random = Random(UInt32(43))
    print("instance_diff_seed:", r3.random() != r4.random())

    # Per-instance doesn't perturb the singleton.
    random.seed(Int32(99))
    before: float = random.random()
    side: Random = Random(UInt32(12345))
    side.random()
    side.random()
    random.seed(Int32(99))
    after: float = random.random()
    print("singleton_isolated:", before == after)

    # --- Integer helpers.
    random.seed(Int32(42))
    print("randint:")
    for _ in range(5):
        print(random.randint(Int32(1), Int32(100)))

    random.seed(Int32(42))
    print("randrange_stop:")
    for _ in range(3):
        print(random.randrange(Int32(10)))

    random.seed(Int32(42))
    print("randrange_start_stop:")
    for _ in range(3):
        print(random.randrange(Int32(100), Int32(200)))

    random.seed(Int32(42))
    print("randrange_step:")
    for _ in range(3):
        print(random.randrange(Int32(0), Int32(100), Int32(7)))

    # Power-of-2 widths exercise the _randbelow bit_length(n) path, which
    # differs from bit_length(n-1) only for powers of 2. Earlier bug lived
    # here: widths 100, 10, 7-step (all non-powers-of-2) matched CPython
    # by coincidence while power-of-2 widths did not.
    random.seed(Int32(42))
    print("randrange_pow2:")
    for _ in range(5):
        print(random.randrange(Int32(4)))
    random.seed(Int32(42))
    print("randrange_pow2_32:")
    for _ in range(3):
        print(random.randrange(Int32(0), Int32(32)))

    random.seed(Int32(42))
    print("randbytes_multiples_of_4:")
    print(random.randbytes(Int32(16)).hex())
    # Non-multiple-of-4 sizes exercise the partial-word packing path.
    # Earlier bug took low bytes of the final word; CPython uses the top
    # bytes (`getrandbits(k)` shifts the final word by `32 - k` before
    # packing, so the bytes surfaced are the high end).
    random.seed(Int32(42))
    print("randbytes_1:", random.randbytes(Int32(1)).hex())
    random.seed(Int32(42))
    print("randbytes_5:", random.randbytes(Int32(5)).hex())
    random.seed(Int32(42))
    print("randbytes_7:", random.randbytes(Int32(7)).hex())

    # --- Continuous distributions.
    # Each block: seed, N draws, scaled-int printed.
    random.seed(Int32(42))
    print("uniform:")
    for _ in range(3):
        _p(random.uniform(1.0, 10.0))

    random.seed(Int32(42))
    print("triangular_default:")
    for _ in range(3):
        _p(random.triangular())

    random.seed(Int32(42))
    print("triangular_args:")
    for _ in range(3):
        _p(random.triangular(0.0, 10.0, 3.0))

    random.seed(Int32(42))
    print("gauss:")
    for _ in range(4):
        _p(random.gauss(0.0, 1.0))

    random.seed(Int32(42))
    print("normalvariate:")
    for _ in range(3):
        _p(random.normalvariate(0.0, 1.0))

    random.seed(Int32(42))
    print("lognormvariate:")
    for _ in range(3):
        _p(random.lognormvariate(0.0, 1.0))

    random.seed(Int32(42))
    print("expovariate:")
    for _ in range(3):
        _p(random.expovariate(1.0))

    random.seed(Int32(42))
    print("paretovariate:")
    for _ in range(3):
        _p(random.paretovariate(2.0))

    random.seed(Int32(42))
    print("weibullvariate:")
    for _ in range(3):
        _p(random.weibullvariate(1.0, 1.5))

    random.seed(Int32(42))
    print("gammavariate_big:")
    for _ in range(3):
        _p(random.gammavariate(2.0, 1.0))

    random.seed(Int32(42))
    print("gammavariate_small:")
    for _ in range(3):
        _p(random.gammavariate(0.5, 1.0))

    random.seed(Int32(42))
    print("betavariate:")
    for _ in range(3):
        _p(random.betavariate(2.0, 5.0))

    random.seed(Int32(42))
    print("vonmisesvariate:")
    for _ in range(3):
        _p(random.vonmisesvariate(0.0, 1.0))

    # gauss() state is cleared on reseed (CPython matches).
    random.seed(Int32(42))
    g1: float = random.gauss(0.0, 1.0)
    random.seed(Int32(42))
    g2: float = random.gauss(0.0, 1.0)
    print("gauss_state_reset:", g1 == g2)


main()
