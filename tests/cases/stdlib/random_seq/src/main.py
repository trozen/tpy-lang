# random.choice / shuffle, getrandbits(k > 32), seed(negative).
# All byte-identical to CPython on the same MT seed -- choice and
# shuffle each consume known _randbelow(...) draws, getrandbits packs
# multi-word output little-endian like CPython's getrandbits, and
# negative seeds map to abs(seed) before init_by_array.
import random
from tpy import int32

def main() -> None:
    # --- choice: byte-identical to CPython on the same seed.
    random.seed(int32(42))
    items: list[int32] = [int32(10), int32(20), int32(30), int32(40), int32(50)]
    print("choice_seed42:")
    i: int32 = 0
    while i < 8:
        print(random.choice(items))
        i += 1

    # choice on str list works too.
    random.seed(int32(7))
    words: list[str] = ["alpha", "beta", "gamma", "delta"]
    print("choice_str:")
    i = 0
    while i < 6:
        print(random.choice(words))
        i += 1

    # --- shuffle: in-place permutation, byte-identical to CPython.
    random.seed(int32(99))
    deck: list[int32] = [int32(0), int32(1), int32(2), int32(3), int32(4),
                        int32(5), int32(6), int32(7), int32(8), int32(9)]
    random.shuffle(deck)
    print("shuffle_99:")
    for v in deck:
        print(v)

    # Shuffle preserves the multiset: the sum is invariant across any
    # permutation of [1..5], so it must equal 15 regardless of seed.
    random.seed(int32(123))
    arr: list[int32] = [int32(1), int32(2), int32(3), int32(4), int32(5)]
    random.shuffle(arr)
    s: int32 = 0
    for v in arr:
        s += v
    print("shuffle_multiset_sum:", s)

    # Edge cases: shuffle should be a no-op on lengths 0 and 1 (the
    # while-loop body never executes for either).
    empty: list[int32] = []
    random.shuffle(empty)
    print("shuffle_empty_len:", int32(len(empty)))
    single: list[int32] = [int32(42)]
    random.shuffle(single)
    print("shuffle_single:", single[0])

    # --- getrandbits: now returns int (BigInt). k <= 32 fast path matches
    # the previous uint32 stream byte-for-byte; k > 32 concatenates words.
    random.seed(int32(42))
    print("getrandbits_32:")
    i = 0
    while i < 4:
        print(random.getrandbits(int32(32)))
        i += 1

    # k=33 exercises the multi-word boundary (numwords=2, last_k=1).
    random.seed(int32(42))
    print("getrandbits_33:")
    i = 0
    while i < 3:
        print(random.getrandbits(int32(33)))
        i += 1

    random.seed(int32(42))
    print("getrandbits_64:")
    i = 0
    while i < 3:
        print(random.getrandbits(int32(64)))
        i += 1

    random.seed(int32(42))
    print("getrandbits_100:")
    i = 0
    while i < 2:
        print(random.getrandbits(int32(100)))
        i += 1

    # Small k still works.
    random.seed(int32(42))
    print("getrandbits_small:")
    print(random.getrandbits(int32(1)))
    print(random.getrandbits(int32(7)))

    # --- seed(negative): mapped to abs(), so seed(-42) == seed(42) stream.
    random.seed(int32(-42))
    a0: int = random.getrandbits(int32(32))
    random.seed(int32(42))
    b0: int = random.getrandbits(int32(32))
    print("seed_neg_eq_pos:", a0 == b0)

    # INT32_MIN -> 2^31 absolute value (the only negative whose abs
    # doesn't fit in int32). Just verify it doesn't crash and produces
    # a reproducible stream.
    random.seed(int32(-2147483648))
    c0: int = random.getrandbits(int32(32))
    random.seed(int32(-2147483648))
    c1: int = random.getrandbits(int32(32))
    print("seed_int32min_reproducible:", c0 == c1)

main()
