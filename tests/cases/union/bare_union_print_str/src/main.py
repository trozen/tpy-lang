# print()/str() of a bare union value (value-union and pointer-variant record
# union); the record case mutates after narrowing to prove print aliases it.
from tpy import int32


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __repr__(self) -> str:
        return "Counter(" + str(self.n) + ")"


def main() -> None:
    a: int32 | str = 5
    print(a)  # tpyc: ok
    print(str(a))  # tpyc: ok
    a = "hi"
    print(a)
    print(str(a))

    # Pointer-variant record union: print after a narrow+mutate observes the
    # live object (a silent copy at the print boundary would print Counter(1)).
    u: Counter | str = Counter(1)
    print(u)  # tpyc: ok
    print(str(u))  # tpyc: ok
    if isinstance(u, Counter):
        u.n = 99
    print(u)


main()
