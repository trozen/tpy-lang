# `in` and for-iteration over a readonly set (the ReadonlyType iterability
# unwrap covers set, not only dict).
from tpy import readonly, int32


def has(s: readonly[set[int32]], x: int32) -> bool:
    return x in s


def count(s: readonly[set[int32]]) -> int32:
    n = 0
    for _e in s:
        n += 1
    return n


def main() -> None:
    s: set[int32] = {1, 2, 3}
    print(has(s, 2), has(s, 9))
    print(count(s))


main()
