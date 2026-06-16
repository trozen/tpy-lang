# `in` and for-iteration over a readonly set (the ReadonlyType iterability
# unwrap covers set, not only dict).
from tpy import readonly, Int32


def has(s: readonly[set[Int32]], x: Int32) -> bool:
    return x in s


def count(s: readonly[set[Int32]]) -> Int32:
    n = 0
    for _e in s:
        n += 1
    return n


def main() -> None:
    s: set[Int32] = {1, 2, 3}
    print(has(s, 2), has(s, 9))
    print(count(s))


main()
