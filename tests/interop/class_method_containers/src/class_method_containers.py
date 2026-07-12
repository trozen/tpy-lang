# tpy: ext_module
# Containers and Span[T] at the exposed-class method boundary: __init__ and
# method params/returns admit the same recursively-marshalled container set as
# free @export functions (O(n) by-copy, docs/CPYTHON_INTEROP.md "container
# cliff"), and a numeric Span crosses as a method PARAM via the buffer
# protocol. The copy divergences (param mutation invisible, borrow-return not
# aliased) are asserted in ext_checks.py, where they differ from the aliasing
# CPython source.
from tpy import Int64, Own, Span, readonly
from tpy.extern import export


@export
class Stats:
    total: Int64

    def __init__(self, seed: list[Int64]):
        self.total = 0
        for x in seed:
            self.total += x

    def add_dict(self, d: dict[str, Int64]) -> Int64:
        for v in d.values():
            self.total += v
        return self.total

    def add_span(self, xs: Span[readonly[Int64]]) -> None:
        for x in xs:
            self.total += x

    def span_sum(self, xs: Span[Int64]) -> Int64:
        s: Int64 = 0
        for x in xs:
            s += x
        return s

    def scaled(self, xs: list[Int64], k: Int64) -> Own[list[Int64]]:
        out: list[Int64] = []
        for x in xs:
            out.append(x * k)
        return out

    def uniq(self, xs: list[Int64]) -> Own[set[Int64]]:
        out: set[Int64] = set()
        for x in xs:
            out.add(x)
        return out

    def snapshot(self) -> tuple[Int64, str]:
        return (self.total, "total")

    def flatten(self, m: dict[str, list[Int64]]) -> Own[list[Int64]]:
        out: list[Int64] = []
        for v in m.values():
            for x in v:
                out.append(x)
        return out

    def absorb(self, xs: list[Int64], v: Int64) -> Int64:
        # Mutates a copy-in param: warns, and the growth is invisible to the
        # caller (proven in ext_checks.py). Aliases in the source.
        xs.append(v)
        return len(xs)

    def same(self, xs: list[Int64]) -> list[Int64]:
        # Borrow-form container return: warns, and the boundary hands back a
        # fresh copy, not an alias of xs (proven in ext_checks.py).
        return xs
