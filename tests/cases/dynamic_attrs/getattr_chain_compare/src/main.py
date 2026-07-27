# A `__getattr__` fallback is the other hidden call behind field-access syntax,
# so it must bind a chained-compare temp for the same reason a property does --
# inlining ran the fallback twice for one source-level attribute read.
calls = 0


class Bag:
    _base: int

    def __init__(self, base: int) -> None:
        self._base = base

    def __getattr__(self, name: str) -> int:
        global calls
        calls += 1
        return self._base


def main() -> None:
    global calls
    b = Bag(5)

    calls = 0
    in_range = 1 < b.anything < 10
    print("in range:", in_range, "calls:", calls)

    calls = 0
    member = b.anything in (5, 9)
    print("membership:", member, "calls:", calls)


main()
