# A use that needs a literal-seeded local's type on the spot (a list element)
# decides it from the stores seen so far; a later wider store is refused and
# names that use and the annotation that keeps the program.
def big() -> int:
    return 10 ** 20


def main() -> None:
    n = 0
    xs = [n]
    print(xs)
    # the wider store after the list element fixed `n` as int32
    n = big()  # tpyc: error(/'n' was used as int32 at line 10 \(a list element\), and this value is int; annotate its first binding: n: int = 0/)
    print(n)


main()
