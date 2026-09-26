# A tuple-unpack comprehension over a user iterable stays rejected, like the
# tuple-unpack `for` over one (BUGS.md#unpack-over-user-iterable).
from typing import Iterator
from tpy import int32


class PBag:
    items: list[tuple[str, int32]]

    def __init__(self) -> None:
        self.items = [("a", 1), ("b", 2)]

    def __iter__(self) -> Iterator[tuple[str, int32]]:
        return iter(self.items)


def main() -> None:
    p = PBag()
    print([k for k, v in p])  # tpyc: error(/not yet supported by C\+\+ code generation/)


main()
