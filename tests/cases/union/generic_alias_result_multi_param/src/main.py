# Two-parameter generic alias used with isinstance narrowing on the
# expanded union members (not on the alias name itself).
from tpy import Int32

type Result[T, E] = T | E


def parse_int(s: str) -> Result[Int32, str]:
    if s == "42":
        return Int32(42)
    return "bad input"


def main() -> None:
    r = parse_int("42")  # tpyc: type(/Int32 \| str/)
    if isinstance(r, Int32):
        print(r)
    else:
        print(r)
    r2 = parse_int("oops")  # tpyc: type(/Int32 \| str/)
    if isinstance(r2, Int32):
        print(r2)
    else:
        print(r2)


main()
