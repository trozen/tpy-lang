# A non-generic recursive-union wrapper follows the reference-type return
# convention too: a bare `-> Expr` return is `Expr&`, so returning a fresh
# value would dangle. Sema requires Own[Expr].
type Expr = int | list[Expr]


def make_branch() -> Expr:
    return [1, 2]  # tpyc: error(/returned by reference.*Use Own\[Expr\]/)


def main() -> None:
    make_branch()


main()
