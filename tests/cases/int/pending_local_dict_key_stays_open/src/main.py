# A dict key is the `__getitem__` call's argument: a literal-seeded local
# passed there stays open, and a later wider store widens it (as the explicit
# `d.__getitem__(p)` does).
def big() -> int:
    return 10 ** 20


def main(d: dict[int, str]) -> None:
    p = 0
    print(d[p])  # tpyc: ok
    # the wider store after the key widens `p` to int
    p = big()  # tpyc: ok
    print(p)


main({0: "zero"})
