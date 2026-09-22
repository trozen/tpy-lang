# A container local aliased at its source's LAST USE moves into the alias
# (`std::vector<int32_t> xs = std::move(ys);`), the same owning decl a record
# pointee has always taken. The @nocopy-element section is the guard: a decl
# that copied instead of moving would be a compile error there, not a silent
# duplicate the output could not tell apart.
from typing import Iterator

from tpy import int32, nocopy, readonly


@nocopy
class Tag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# free function, list of scalars
def list_scalars() -> None:
    ys: list[int32] = [9]
    xs = ys  # tpyc: ok
    xs.append(3)
    print("list", len(xs), xs[0], xs[1])


# free function, @nocopy element -- a copy here would not compile
def nocopy_elements() -> None:
    ys: list[Tag] = []
    ys.append(Tag(1))
    xs = ys  # tpyc: ok
    xs.append(Tag(2))
    print("nocopy", len(xs), xs[0].n, xs[1].n)


# free function, dict / set / bytearray / nested list
def other_kinds() -> None:
    ds: dict[str, int32] = {"a": 1}
    d2 = ds  # tpyc: ok
    d2["b"] = 2
    st: set[int32] = {1}
    s2 = st  # tpyc: ok
    s2.add(2)
    ba = bytearray(b"ab")
    b2 = ba  # tpyc: ok
    b2.append(99)
    ns: list[list[int32]] = [[9]]
    n2 = ns  # tpyc: ok
    n2.append([1])
    print("kinds", len(d2), len(s2), len(b2), len(n2))


class Holder:
    total: int32

    # constructor
    def __init__(self) -> None:
        ys: list[int32] = [4]
        xs = ys  # tpyc: ok
        xs.append(5)
        self.total = xs[0] + xs[1]

    # method
    def run(self) -> None:
        ys: dict[int32, int32] = {1: 2}
        xs = ys  # tpyc: ok
        xs[3] = 4
        print("method", len(xs))


# free function, source READ after the alias -- a borrow, not a move
def read_after() -> None:
    ys: list[int32] = [7]
    xs = ys  # tpyc: ok
    xs.append(8)
    print("alias", len(ys), len(xs))


# free function, readonly source -- the alias binds a const reference
def readonly_source(ys: readonly[list[int32]]) -> None:
    xs = ys  # tpyc: ok
    print("readonly", len(xs))


# generator
def in_generator() -> Iterator[int32]:
    ys: list[int32] = [6]
    xs = ys  # tpyc: ok
    xs.append(7)
    yield xs[0] + xs[1]


def main() -> None:
    list_scalars()
    nocopy_elements()
    other_kinds()
    h = Holder()
    print("constructor", h.total)
    h.run()
    read_after()
    src: list[int32] = [1, 2]
    readonly_source(src)
    for v in in_generator():
        print("generator", v)


main()
