# A membership test is no operator a literal-seeded local's type waits for:
# `in` needs the operand's type on the spot, so a later wider store is
# refused naming it.
def big() -> int:
    return 10 ** 20


def main(xs: list[int]) -> None:
    p = 0
    print((p + 1) in xs)
    # the wider store after `in` fixed `p` as int32
    p = big()  # tpyc: error(/'p' was used as int32 at line 10 \(an operand of 'in'\), and this value is int; annotate its first binding: p: int = 0/)
    print(p)


main([1])
