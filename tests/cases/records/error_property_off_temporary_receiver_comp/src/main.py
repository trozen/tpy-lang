# A generator expression over a borrow-returning @property read off a fresh
# receiver is refused (BUGS.md#genexpr-property-off-fresh-receiver-rejects).
from tpy import Own, int32

G: list[int32] = [1, 2]


class H:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    @property
    def items(self) -> list[int32]:
        return G


def mk() -> Own[H]:
    return H()


def main() -> None:
    print(sum(x for x in mk().items))  # tpyc: error(/code generation \(expr\.genexpr\)/)


main()
