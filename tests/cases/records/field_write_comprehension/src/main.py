# A list / dict / set comprehension written into CONTAINER STORAGE: a plain
# container field (in a method, a free function, a constructor body past its
# member-init prefix -- the doom shape -- a generator, a closure and a
# module-level statement), a storage-form `Optional[container]` field, and a
# list / dict / field-container element setitem. The stmt-expr builds the
# slot's own container in place and assigns bare (`this->data = ({ ... });`),
# the render the member-init prefix already had. Every stored container is
# grown afterwards through the slot so a silent copy would show. A nested
# field target (`p.inner.data = ...`) is its own gap, for every container
# source (error_field_write_comprehension_nested_target). The with, try,
# match, async and @error_return positions are not sectioned: the write sinks
# are position-independent, and the generator, closure and module-level
# sections cover the positions with a different variable model.
from typing import Iterator, Optional
from tpy import int32


class Pic:
    data: list[list[int32]]
    flat: list[int32]
    d: dict[str, int32]
    s: set[int32]
    opt: Optional[list[int32]]

    def __init__(self) -> None:
        self.data = []
        self.flat = []
        self.d = {}
        self.s = set()
        self.opt = None

    # Method body, a nested comprehension.
    def fill(self, w: int32, h: int32) -> None:
        self.data = [[0 for k in range(h)] for j in range(w)]  # tpyc: ok


class Grid:
    width: int32
    height: int32
    data: list[list[int32]]

    # The doom shape: locals first, so the field writes land in the
    # constructor BODY rather than the member-init prefix.
    def __init__(self, raw: list[int32]) -> None:
        width = raw[0]
        height = raw[1]
        self.width = width
        self.height = height
        self.data = [[0 for k in range(height)] for j in range(width)]  # tpyc: ok
        for j in range(width):
            self.data[j][0] = j + 1


# Free function: the dict and set comprehensions, and the Optional field.
def fill_free(p: Pic, n: int32) -> None:
    p.d = {str(j): j for j in range(n)}  # tpyc: ok
    p.s = {j * 2 for j in range(n)}  # tpyc: ok
    p.opt = [j for j in range(n)]  # tpyc: ok


# The Optional field reseated: None, then a second comprehension.
def reseat_opt(p: Pic) -> None:
    p.opt = None
    p.opt = [j * 2 for j in range(2)]  # tpyc: ok
    if p.opt is not None:
        p.opt.append(6)
        print("optional_reseat", p.opt)


# Element setitems: a local list, a dict, and a field container.
def setitems(p: Pic, n: int32) -> None:
    rows: list[list[int32]] = [[], []]
    rows[0] = [j for j in range(n)]  # tpyc: ok
    rows[0].append(7)
    print("setitem_list", rows)
    # The bounds-proven index takes the direct element assign.
    for i in range(len(rows)):
        rows[i] = [j + i for j in range(2)]  # tpyc: ok bounds_safe(rows)
        rows[i].append(8)
    print("setitem_list_safe", rows)
    d: dict[str, list[int32]] = {}
    d["a"] = [j for j in range(n)]  # tpyc: ok
    d["a"].append(7)
    print("setitem_dict", d)
    p.data[1] = [j + 10 for j in range(n)]  # tpyc: ok
    p.data[1].append(7)
    print("setitem_field", p.data[1])


# Generator body.
def gen(p: Pic, k: int32) -> Iterator[int32]:
    for i in range(k):
        p.flat = [i + j for j in range(2)]  # tpyc: ok
        p.flat.append(9)
        yield len(p.flat)


# Closure body.
def closure(p: Pic) -> None:
    def inner(n: int32) -> int32:
        p.flat = [j for j in range(n)]  # tpyc: ok
        p.flat.append(1)
        return len(p.flat)

    print("closure", inner(4), p.flat)


def main() -> None:
    p = Pic()
    p.fill(3, 2)
    p.data[1].append(7)
    print("method", len(p.data), p.data[1])
    g = Grid([3, 2])
    g.data[2].append(8)
    print("ctor_body", g.data)
    fill_free(p, 3)
    p.d["z"] = 9
    p.s.add(9)
    print("free_dict", len(p.d), "free_set", len(p.s))
    if p.opt is not None:
        p.opt.append(5)
        print("optional_field", p.opt)
    reseat_opt(p)
    setitems(p, 3)
    for v in gen(p, 2):
        print("generator", v)
    print("generator", p.flat)
    closure(p)


main()

# Module-level statement.
top = Pic()
top.flat = [j * 3 for j in range(3)]  # tpyc: ok
top.flat.append(1)
print("module_level", top.flat)
