# A record appended to a list is stored as a copy (warned), so the element is
# a different object from the original: `is` is False where CPython, which
# aliases, says True -- hence no CPython run.
class A:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def main() -> None:
    a = A(1)
    xs = [A(0)]
    xs.append(a)  # tpyc: warning(/copies/)
    # the stored copy is not the original
    print("append", xs[1] is a, xs[1] is xs[1])  # tpyc: ok


main()
