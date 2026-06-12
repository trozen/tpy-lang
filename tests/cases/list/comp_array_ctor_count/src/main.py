# The Array comprehension build constructs each element in place exactly once:
# a side-effectful __init__ (direct, or reached via a field constructed in the
# element's ctor) ran 2N times under the old default-construct-then-assign
# build vs CPython's N.
log: list[int] = []


class P:
    def __init__(self):
        log.append(1)


class Outer:
    inner: P

    def __init__(self):
        self.inner = P()


def main():
    pts = [P() for i in range(4)]  # tpyc: type(/Array\[P, 4\]/)
    print(len(pts), len(log))
    outs = [Outer() for i in range(3)]  # tpyc: type(/Array\[Outer, 3\]/)
    print(len(outs), len(log))


main()
