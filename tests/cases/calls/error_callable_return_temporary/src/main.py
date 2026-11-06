# A callable's borrow result points into its arguments, so returning it with a
# temporary argument dangles -- the refusal `return ident(P(1))` gets.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Working bullet "A
# callable's return reads as a def's" ("`return f(P(1))` is the
# temporary-return error").
from tpy import Fn, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def ident(p: P) -> P:
    return p


def bad(f: Fn[[P], P]) -> P:
    return f(P(1))  # tpyc: error(/Cannot return local or temporary as reference/)


def main() -> None:
    print(bad(ident).n)


main()
