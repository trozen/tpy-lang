# An `int` too wide for the int64 member it lands in fails int64's own
# range check, as the scalar twin `x: int64 = n` does.
from tpy import int64


def land(n: int) -> None:
    v: int64 | str = n  # tpyc: ok
    print(v)


def main() -> None:
    land(2**70)


main()
