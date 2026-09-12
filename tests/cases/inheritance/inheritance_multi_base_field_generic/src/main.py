# Generic parent field: the T in Box[T].value is substituted based on the
# concrete parent instantiation carried on the child's MRO.
from tpy import int32


class Box[T]:
    value: T


class Label:
    value: str


class Combined(Box[int32], Label):
    def __init__(self, n: int32, s: str) -> None:
        Box.value = n  # Box[int32].value inferred from MRO: int32
        Label.value = s

    def n_plus(self, delta: int32) -> int32:
        v = Box.value  # tpyc: type(int32)
        return v + delta

    def combined(self) -> str:
        return Label.value + "=" + str(Box.value)


def main() -> None:
    c = Combined(7, "n")
    print(c.combined())
    print(c.n_plus(3))


main()
