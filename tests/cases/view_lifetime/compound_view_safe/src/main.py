# Inverse of compound_view_root_mutated: the root-tracking fix must NOT
# over-trigger -- a compound view (ternary / and-or) over params or unmutated
# elements stays a zero-copy view, while an owning-rvalue arm stays owned.


def compound_of_params(a: str, b: str, cond: bool) -> None:
    x = a if cond else b  # tpyc: type(StrView)
    y = a or b  # tpyc: type(StrView)
    print(x, y)


def compound_of_unmutated_elems(c: list[str], d: list[str], cond: bool) -> None:
    # Roots c/d are read but never mutated -> the view is safe and kept.
    x = c[0] if cond else d[0]  # tpyc: type(StrView)
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
