# A pointer-variant union local reseated with a VALUE operator result keeps
# rejecting: the float has no storage to point at and no slot row builds one
# (BUGS.md#ptr-union-local-binop-member-rejects).


def main():
    f: float | dict[str, str] | None = None
    f = 1.5 * 2.0  # tpyc: error(/ptr_union.needs_slot/)
    print(f is None)


main()
