# Ctor member-init-list inits of builtin-container fields: list/dict/set/Array
# literals (empty, str-view elements, record elements, nested lists, Own move)
# and a container-param copy. `ones`/`one_arr` guard the render: the cell is a
# paren direct-init, where a bare `{1}` picked the vector's size ctor on clang.
# The param copy and the record element are INTENTIONAL copies at the
# field-storage boundary (CPython would alias), so only the field's own
# containers are observed, never cross-mutation through the ctor arguments.
from tpy import Array, int32, Own


class Point:
    v: int32

    def __init__(self, v: int32):
        self.v = v


class Holder:
    items: list[int32]
    names: list[str]
    counts: dict[str, int32]
    tags: set[int32]
    arr: Array[int32, 3]
    pts: list[Point]
    grid: list[list[int32]]
    empty_l: list[int32]
    empty_d: dict[int32, int32]
    moved: list[Point]
    copied: list[int32]
    ones: list[int]
    one_arr: Array[int, 1]

    def __init__(self, prefix: str, p: Point, q: Own[Point], copied: list[int32]):
        self.items = [1, 2]
        self.names = [prefix, "lit"]
        self.counts = {"k": 1, "j": 2}
        self.tags = {10, 20}
        self.arr = [3, 4, 5]
        self.pts = [Point(1), p]  # tpyc: warning(/copies Point into owned storage/)
        self.grid = [[1], [2, 3]]
        self.empty_l = []
        self.empty_d = {}
        self.moved = [q]
        self.copied = copied  # tpyc: warning(/copies list\[int32\] into field/)
        self.ones = [1]  # tpyc: ok -- one BigInt element: must not become a size
        self.one_arr = [1]  # tpyc: ok -- the Array sibling of the same shape


def main():
    h = Holder("pre", Point(7), Point(9), [40, 41])
    # Mutate the field-owned containers after construction and observe.
    h.items.append(3)
    h.tags.add(30)
    h.grid[0].append(6)
    h.empty_l.append(99)
    print(h.items, len(h.empty_l), len(h.empty_d))
    print(h.names[0], h.names[1], h.counts["k"] + h.counts["j"])
    print(sorted(h.tags), h.arr[0] + h.arr[2])
    print(h.pts[0].v, h.pts[1].v, h.moved[0].v)
    print(h.grid[0], h.grid[1], h.copied)
    h.ones.append(2)
    print("ones", h.ones, len(h.ones), "one_arr", h.one_arr)


main()
