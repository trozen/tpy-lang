# A comprehension loop variable shadowing the live NARROWED union subject: the
# post-comprehension read renames to the extraction alias the comp scope would
# clobber.
from tpy import Int32


def use(v: Int32 | str) -> Int32:
    if isinstance(v, Int32):
        ws = [v for v in range(3)]  # tpyc: error(/expr.list_comp/)
        return len(ws) + v
    return -1


def main() -> None:
    print(use(7))


main()
