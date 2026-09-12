# d.items() / d.values() yield aliases of the stored values (CPython
# semantics): mutation through the loop var reaches the dict. The
# read-only sibling keeps a const receiver (param const-ness inverse).
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32):
        self.x = x


def bump_items(d: dict[str, Point]):
    for k, v in d.items():
        v.x = v.x + 100


def bump_values(d: dict[str, list[int32]]):
    for v in d.values():
        v.append(9)


def total(d: dict[str, list[int32]]) -> int32:
    n = 0
    for k, v in d.items():
        n = n + len(v)
    return n


def main():
    pts = {"a": Point(1), "b": Point(2)}
    bump_items(pts)
    print(pts["a"].x, pts["b"].x)

    lists: dict[str, list[int32]] = {}
    lists["a"] = [1]
    lists["b"] = [2, 3]
    bump_values(lists)
    print(lists["a"], lists["b"])
    print(total(lists))


main()
