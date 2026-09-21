# A generator expression reading a `global`-declared name. The name is a
# namespace-scope object, so the frame body NAMES it; it is not a capture.
from tpy import int32

limit: int32 = int32(3)
scale: int32 = int32(2)


def bump() -> int32:
    global limit
    limit = int32(4)
    # The subject: the filter reads the `global`-declared name, and the
    # element expression reads a module global the function only reads.
    return sum(i * scale for i in range(6) if i < limit)  # tpyc: ok


def main() -> None:
    print(bump(), limit)


main()
