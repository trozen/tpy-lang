# The reference ternary's element leg over a bytearray element: both arms are
# container-element lvalues, so the C++ `?:` is an lvalue the decl ALIASES.
# The plain element read (`x = d["a"]`) is still rejected at this element
# family, so the ternary is the only admitted spelling for it today.


def pick(d: dict[str, bytearray], cond: bool) -> None:
    # The subject: an element-subscript arm pair binds the element by
    # reference, so the write below lands in the dict, not in a copy.
    x = d["a"] if cond else d["b"]  # tpyc: ok
    x[0] = 90
    # A second alias of the same element observes the write.
    y = d["a"] if cond else d["b"]
    print(len(y), y[0])


def main() -> None:
    d: dict[str, bytearray] = {}
    d["a"] = bytearray(b"ab")
    d["b"] = bytearray(b"cde")
    pick(d, True)
    pick(d, False)
    pick(d, True)


main()
