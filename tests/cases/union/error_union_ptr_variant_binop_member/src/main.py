# A pointer-variant union local reseated with a BINOP rvalue keeps
# rejecting: only call / method-call member rvalues have a verified slot
# render, so the operator result is a later rung.


def main():
    f: float | dict[str, str] | None = None
    f = 1.5 * 2.0  # tpyc: error(/decl.ptr_union_source/)
    print(f is None)


main()
