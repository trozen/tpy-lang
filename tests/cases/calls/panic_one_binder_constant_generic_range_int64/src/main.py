# A direct conversion of an integer type argument into a target too narrow
# for it is checked: `c: int64 = N` at 2**63 raises OverflowError, never wraps.
from tpy import int64


class Box[N: int]:
    def run(self) -> None:
        # One checked conversion from `N` to int64.
        c: int64 = N  # tpyc: ok
        print(c)


def main() -> None:
    Box[9223372036854775808]().run()


main()
