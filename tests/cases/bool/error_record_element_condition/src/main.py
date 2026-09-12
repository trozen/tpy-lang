# A record ELEMENT with no truthiness dunder in a condition: the fold to `true`
# would discard the read that carries the bounds check, so the condition rejects.
from tpy import int32


class Rec:
    n: int32

    def __init__(self) -> None:
        self.n = 1


def probe(rs: list[Rec]) -> int32:
    if rs[0]:  # tpyc: error(/cond\.subscript/)
        return 1
    return 0


def main() -> None:
    print(probe([Rec()]))


main()
