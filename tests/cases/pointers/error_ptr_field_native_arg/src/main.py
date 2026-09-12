# The adjacent Ptr-member read that keeps rejecting: a container member of a
# `Ptr[record]` binding passed to a builtin, whose arg rows admit the member
# read only off a reference receiver.
from tpy import int32, Ptr


class Sector:
    flags: list[bool]

    def __init__(self) -> None:
        self.flags = [True, False, True]


def count(sector: Ptr[Sector]) -> int32:
    return int32(len(sector.flags))  # tpyc: error(/native_arg.container/)


def main():
    s = Sector()
    p: Ptr[Sector] = s
    print(count(p))


main()
