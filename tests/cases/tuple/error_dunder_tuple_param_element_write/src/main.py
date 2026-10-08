# An operator dunder is implicitly readonly, and readonly reaches the borrowed
# element of its tuple parameter as it reaches a reference parameter: the
# write through the unpacked element is refused. (`@readonly(False)` passes
# sema but does not build: BUGS.md#mutating-dunder-const-wrapper.)
from tpy import Own, int32, nocopy


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class Tok:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def take(t: Own[Tok]) -> int32:
    return t.n


class Adder:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def __add__(self, o: tuple[Own[Tok], Box]) -> int32:
        a, c = o
        c.n += 1  # tpyc: error(/Cannot mutate readonly reference/)
        return self.k + take(a)


def main() -> None:
    print(Adder(1).k)


main()
