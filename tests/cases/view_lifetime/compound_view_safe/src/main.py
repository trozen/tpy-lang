# Inverse of compound_view_root_mutated: a compound (ternary / and-or) over params
# stays a zero-copy view, while an element arm or an owning-rvalue arm owns.


def compound_of_params(a: str, b: str, cond: bool) -> None:
    x = a if cond else b  # tpyc: type(StrView)
    y = a or b  # tpyc: type(StrView)
    print(x, y)


def compound_of_unmutated_elems(c: list[str], d: list[str], cond: bool) -> None:
    # Container elements never lend under the one view rule, so even unmutated
    # roots c/d give an owned copy.
    x = c[0] if cond else d[0]  # tpyc: type(str)
    print(x, c[0], d[0])


def owning_rvalue_arm(a: str, cond: bool) -> None:
    # mk() returns a fresh owned str; the result cannot be a single borrowed
    # view, so the whole local must own a copy.
    x = a if cond else mk()  # tpyc: type(str)
    print(x)


def mk() -> str:
    return "fresh"


def main() -> None:
    compound_of_params("hi", "yo", True)
    compound_of_unmutated_elems(["aa"], ["bb"], False)
    owning_rvalue_arm("kept", True)


main()
