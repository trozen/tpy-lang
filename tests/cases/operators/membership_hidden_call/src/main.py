# The `x in (a, b)` needle renders once per element, so anything but a name or
# literal binds to a temp first. A property getter needle must call the getter
# once; a plain field binds too (this site is stricter than the chained
# compare, which inlines the same field); a name inlines. The snapshot pins all
# three shapes.
calls = 0


def bump() -> int:
    global calls
    calls += 1
    return 5


class P:
    plain: int

    def __init__(self) -> None:
        self.plain = 7

    @property
    def probe(self) -> int:
        return bump()


def main() -> None:
    global calls
    p = P()

    calls = 0
    hit = p.probe in (5, 9)
    print("property hit:", hit, "calls:", calls)

    calls = 0
    miss = p.probe not in (1, 2)
    print("property miss:", miss, "calls:", calls)

    # A single-element tuple never duplicates the needle, temp or not.
    calls = 0
    single = p.probe in (5,)
    print("single:", single, "calls:", calls)

    # A plain field has no hidden call but still binds here.
    print("plain field:", p.plain in (6, 7))

    # Inverse: a name is trivial enough to render into each comparison.
    n = p.plain
    print("name needle:", n in (6, 7))


main()
