# Recursive-union wrapper params get C++ const-ness from normal inference, like
# records: returned-by-reference infers mutable Expr&, read-only infers const Expr&.
from tpy import int32

type Expr = int | list[Expr]


def passthru(e: Expr) -> Expr:   # returned by reference -> Expr& e
    return e


def count(e: Expr) -> int32:     # only read -> const Expr& e
    if isinstance(e, int):
        return 1
    n = 0
    for sub in e:
        n += count(sub)
    return n


def main() -> None:
    tree: Expr = [1, [2, 3], 4]
    print(count(tree))
    print(count(passthru(tree)))


main()
