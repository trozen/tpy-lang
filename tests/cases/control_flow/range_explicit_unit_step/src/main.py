# range() with an explicitly spelled step: +1 collapses to the ascending loop
# and -1 to the descending one, so both must count the same elements as the
# two-argument form.
from tpy import int32


def up(n: int32) -> int32:
    acc = 0
    for i in range(0, n, 1):  # a literal +1 step
        acc = acc + i
    return acc


def down(n: int32) -> int32:
    acc = 0
    for i in range(n, 0, -1):  # a literal -1 step
        acc = acc + i
    return acc


def main() -> None:
    print(up(5), down(5))


main()
