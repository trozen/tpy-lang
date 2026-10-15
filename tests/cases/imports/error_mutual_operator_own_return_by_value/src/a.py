from tpy import Own, int32
import b


class Adder:
    # The friend `operator+` returns the owning `Item | None` by value, which
    # `a.hpp` sees only forward-declared.
    def __add__(self, x: b.Item) -> Own[b.Item | None]:  # tpyc: error(/Cyclic import: return type/)
        if x.n > 0:
            return b.Item(x.n + 1)
        return None


def run() -> int32:
    r = Adder() + b.Item(2)
    if r is None:
        return 0
    return r.n
