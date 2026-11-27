# A fresh argument at a TPy body's structural protocol parameter passes
# inline, as at the callee's concrete twin; other positions in the parts.
from tpy import int32
from fixtures import (Acc, Bag, bump_twice, bump_twice_c, total, total_b,
                      total_l)
import part_more
import part_positions
import part_receiver


def free_fn(k: int32) -> None:
    # range at Iterable[int32]; its twin's `Range[int32]` parameter has no
    # CPython spelling, so the C++ to match is `total(auto(Range(5)))`
    print("free range", total(range(5)))  # tpyc: ok
    # comprehension at Iterable[int32]; twin: total_l([...])
    print("free comp", total([i * 2 for i in range(k)]),  # tpyc: ok
          total_l([i * 2 for i in range(k)]))
    # list literal at Iterable[int32]; twin: total_l([1, 2, 3])
    print("free literal", total([1, 2, 3]), total_l([1, 2, 3]))  # tpyc: ok
    # fresh record at Iterable[int32]; twin: total_b(Bag(4))
    print("free record", total(Bag(4)), total_b(Bag(4)))  # tpyc: ok
    # fresh record at a user protocol, written through it; twin: bump_twice_c
    print("free proto", bump_twice(Acc(k)), bump_twice_c(Acc(k)))  # tpyc: ok
    # a conditional of two comprehensions at Iterable[int32]
    print("free cond comp",
          total([i for i in range(k)] if k > 2 else [i * 3 for i in range(k)]))  # tpyc: ok


def main() -> None:
    free_fn(3)
    part_positions.main()
    part_more.main()
    part_receiver.main()


main()
