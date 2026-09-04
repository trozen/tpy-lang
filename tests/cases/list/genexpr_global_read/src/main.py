# A generator expression reading a `global`-declared name. The name is a
# namespace-scope object, so the lambda must NAME it, not capture it -- a
# capture of one is ill-formed C++ (clang rejects it outright).
from tpy import Int32

limit: Int32 = Int32(3)
scale: Int32 = Int32(2)


def bump() -> Int32:
    global limit
    limit = Int32(4)
    # The subject: the filter reads the `global`-declared name, and the
    # element expression reads a module global the function only reads.
    return sum(i * scale for i in range(6) if i < limit)  # tpyc: ok


def main() -> None:
    print(bump(), limit)


main()
