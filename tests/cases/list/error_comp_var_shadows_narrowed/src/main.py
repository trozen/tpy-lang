# A comprehension loop variable shadowing the live NARROWED union subject: the
# post-comprehension read renames to the extraction alias the comp scope would
# clobber.
from tpy import int32


def use(v: int32 | str) -> int32:
    if isinstance(v, int32):
        ws = [v for v in range(3)]  # tpyc: error(/expr.list_comp/)
        return len(ws) + v
    return -1


def main() -> None:
    print(use(7))


main()
