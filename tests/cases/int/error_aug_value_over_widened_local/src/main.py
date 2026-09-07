# An aug-assign VALUE that is a composite over a local widened later in the
# body: the widening applies to the whole body, so the value has no render.
from tpy import Int32


def gi() -> int:
    return 1


def probe() -> None:
    p = 0
    q: Int32 = 7
    q += p + 1  # tpyc: error(/aug_assign/)
    print(q)
    p = gi()
    print(p)


def main() -> None:
    probe()


main()
