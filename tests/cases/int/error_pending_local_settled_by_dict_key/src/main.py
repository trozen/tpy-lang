# A dict key needs its type on the spot, so it decides a literal-seeded local
# from the stores seen so far; a later wider store is refused naming it.
def big() -> int:
    return 10 ** 20


def main(d: dict[int, str]) -> None:
    p = 0
    print(d[p])
    # the wider store after the key fixed `p` as int32
    p = big()  # tpyc: error(/'p' was used as int32 at line 9 \(a subscript key\), and this value is int; annotate its first binding: p: int = 0/)
    print(p)


main({0: "zero"})
