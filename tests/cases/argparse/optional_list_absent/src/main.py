# An optional list-action flag without an explicit default produces
# Optional[list[T]] = None when the flag is absent (D1: matches CPython
# argparse, where missing append/extend/multi-nargs flags give None
# rather than an empty list).
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    p1 = ArgumentParser()
    p1.add_argument("--tag", action="append")
    p1.add_argument("--num", action="append", type=int)
    a1 = p1.parse_args([])
    if a1.tag is None:
        print("tag none")
    if a1.num is None:
        print("num none")

    p2 = ArgumentParser()
    p2.add_argument("--tag", action="append")
    a2 = p2.parse_args(["--tag", "x", "--tag", "y"])
    assert a2.tag is not None
    print(a2.tag[0])
    print(a2.tag[1])

    p3 = ArgumentParser()
    p3.add_argument("--coord", nargs=2, type=int)
    a3 = p3.parse_args([])
    if a3.coord is None:
        print("coord none")
    return 0


main()
