# A ternary whose arms are a CONTAINER and None at an `Optional[list]` element
# slot: the storage-optional ternary has no container-inner result.
# Concretely, `[[1, 2] if c else None]` as a list-literal element is
# rejected by TPy today.
from typing import Optional
from tpy import Int32


def build(c: bool) -> None:
    xs: list[Optional[list[Int32]]] = [[1, 2] if c else None]  # tpyc: error(/expr.container_literal/)
    print(len(xs))


def main() -> None:
    build(True)


main()
