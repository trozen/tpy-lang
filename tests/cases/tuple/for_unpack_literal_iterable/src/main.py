# `for a, b in [<tuple literals>]:` -- the unpack head over a list LITERAL
# iterable. The literal is captured target-less, so its tuple elements have to
# spell their own type to render at all.
from tpy import Int32


def main() -> None:
    total = 0
    for name, n in [("a", 1), ("b", 2), ("c", 3)]:  # tpyc: ok
        print(name, n)
        total += n
    print(total)
    # The single-var sibling over the same literal: no unpack head.
    for pair in [("x", 4), ("y", 5)]:  # tpyc: ok
        print(pair)
    # An all-scalar element tuple, to keep the str-free shape covered.
    acc = 0
    for a, b in [(1, 2), (3, 4)]:  # tpyc: ok
        acc += a * b
    print(acc)


main()
