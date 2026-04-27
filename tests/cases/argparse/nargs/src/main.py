# argparse nargs in all four shapes (Phase 7E):
#   * nargs='+' on a trailing positional
#   * nargs=N (integer) on an optional flag
#   * extend action paired with nargs (accumulates across calls)
#   * nargs='?' on an optional flag (uses const when bare)
#   * absent optional flag with no default -> Optional[T] field, None
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    p1 = ArgumentParser()
    p1.add_argument("files", nargs="+")
    a1 = p1.parse_args(["a.txt", "b.txt"])
    print(a1.files[0])
    print(a1.files[1])
    print(len(a1.files))

    p2 = ArgumentParser()
    p2.add_argument("--coord", nargs=2, type=int)
    p2.add_argument("--tag", action="extend", nargs="+")
    a2 = p2.parse_args(["--coord", "10", "20", "--tag", "x", "y", "--tag", "z"])
    print(a2.coord[0])
    print(a2.coord[1])
    print(a2.tag[0])
    print(a2.tag[1])
    print(a2.tag[2])

    p3 = ArgumentParser()
    p3.add_argument("--mode", nargs="?", const="auto", default="off")
    a3 = p3.parse_args(["--mode"])
    print(a3.mode)

    p4 = ArgumentParser()
    p4.add_argument("--limit", type=int)
    a4 = p4.parse_args([])
    if a4.limit is None:
        print("none")
    return 0


main()
