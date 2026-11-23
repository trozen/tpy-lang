# A handle passed straight into a declared-readonly slot (`readonly[...]`, `len`, `sum`, `in`)
# keeps the lending call's receiver const; any other consumer makes it mutable.
from typing import Callable, Iterable
from tpy import int32, readonly, Span, auto_readonly
from part_more import main as more_main
from part_writes import main as writes_main
from part_base_call import main as base_call_main


class Counts:
    d: dict[str, int32]

    def __init__(self) -> None:
        self.d = {"a": 1, "b": 2}

    def total(self) -> int32:
        # method: `sum` is @pure, so `total` stays inferred readonly
        return sum(self.d.values())  # tpyc: ok


def n_values(d: dict[str, int32]) -> int32:
    # free function: `len` keeps `d` const
    return len(d.values())  # tpyc: ok


def has(d: dict[str, int32]) -> bool:
    # `in` through a readonly `__contains__` keeps `d` const
    return 2 in d.values()  # tpyc: ok


def count_ro(v: readonly[Iterable[int32]]) -> int32:
    n = 0
    for x in v:
        n += x
    return n


def via_ro(d: dict[str, int32]) -> int32:
    # a readonly parameter keeps `d` const
    return count_ro(d.values())  # tpyc: ok


class H:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]

    @auto_readonly
    def view(self) -> Span[auto_readonly[int32]]:
        return self.xs


def twin_len(h: H) -> int32:
    # a TPy accessor's handle: `h` stays const too
    return len(h.view())  # tpyc: ok


def count(v: Iterable[int32]) -> int32:
    n = 0
    for x in v:
        n += x
    return n


def via_mutable(d: dict[str, int32]) -> int32:
    # keeps demoting: `count`'s parameter is not readonly
    return count(d.values())  # tpyc: ok


def span_sum(s: Span[int32]) -> int32:
    n = 0
    for x in s:
        n += x
    return n


def via_callable(h: H, f: Callable[[Span[int32]], int32]) -> int32:
    # keeps demoting: a callable's body is unknown
    return f(h.view())  # tpyc: ok


def write_through(h: H) -> None:
    # keeps demoting: the handle is bound and written through
    s = h.view()
    s[0] = 5


def main() -> None:
    c = Counts()
    d = {"a": 1, "b": 2}
    h = H()
    print("readonly slot:", c.total(), n_values(d), has(d), via_ro(d),
          twin_len(h))
    write_through(h)
    print("mutable slot:", via_mutable(d), via_callable(h, span_sum), h.xs[0])
    more_main()
    writes_main()
    base_call_main()


main()
