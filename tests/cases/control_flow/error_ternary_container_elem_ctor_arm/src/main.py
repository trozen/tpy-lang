# The reference ternary takes LVALUE arms only. A builtin container's ctor
# call is a prvalue, so a mixed pair would make the C++ `?:` a prvalue that
# silently copies the element arm where CPython aliases it. The arm asks the
# shared rvalue classifier rather than the return-shape predicate, which
# reads a container `__init__` as borrow-returning
# (BUGS.md#void-method-reads-as-borrow-returning); the pair falls through and
# the element read in the fallen-through arm is what names the reject.


def pick(d: dict[str, bytearray], c: bool) -> None:
    x = d["a"] if c else bytearray(b"z")  # tpyc: error(/elem\.record_nonf1/)
    print(len(x))


def main() -> None:
    d: dict[str, bytearray] = {}
    d["a"] = bytearray(b"ab")
    pick(d, True)


main()
