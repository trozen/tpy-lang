# The tuple-unpack for-head shapes: dict items, a list of tuples, and a
# discarded second target.
from tpy import int32


def sum_items(d: dict[int32, int32]) -> int32:
    s = 0
    for k, v in d.items():
        s = s + k + v
    return s


def sum_pairs(ps: list[tuple[int32, int32]]) -> int32:
    s = 0
    for a, b in ps:
        s = s + a * b
    return s


def discard_snd(ps: list[tuple[int32, int32]]) -> int32:
    s = 0
    for a, _ in ps:  # the second target is discarded
        s = s + a
    return s


def main() -> None:
    d = {1: 10, 2: 20}
    ps = [(1, 2), (3, 4)]
    print(sum_items(d), sum_pairs(ps), discard_snd(ps))


main()
