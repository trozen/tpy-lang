# Tuple-unpack for-loop with pointer-repr Optional elements
# (tuple[P | None, P | None]) read across a yield -- exercises the
# optional_to_ptr target binding (distinct from the plain-reference alias).
from typing import Iterator, Optional
from tpy import Int32


class P:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def gen(pairs: list[tuple[Optional[P], Optional[P]]]) -> Iterator[Int32]:  # tpyc: ok
    for a, b in pairs:
        if a is not None:
            yield a.x
        if b is not None:
            yield b.x


def main() -> None:
    data: list[tuple[Optional[P], Optional[P]]] = [(P(1), None), (None, P(4))]
    for v in gen(data):
        print(v)


main()
