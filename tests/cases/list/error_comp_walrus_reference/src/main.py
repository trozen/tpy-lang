# A walrus binding an object inside a comprehension is refused whatever the
# source (docs/LANGUAGE_FEATURES.md, comprehension rules); CPython runs it.


class C:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def main() -> None:
    xs = [C(1), C(2)]
    # the walrus target would keep a reference to an element past the comprehension
    out = [c for c in xs if (last := c).n > 0]  # tpyc: error(/a walrus inside a comprehension cannot bind 'last' to a reference to an object; use a `for` loop/)
    print(len(out), last.n)


main()
