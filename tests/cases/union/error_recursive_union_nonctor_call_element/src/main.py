# A non-ctor CALL element inside a recursive-union literal is outside the element
# family, so the literal rejects.
from dataclasses import dataclass
from tpy import int32, Own


@dataclass
class Leaf:
    value: int32


type Tree = Leaf | list[Tree]


def make_leaf() -> Own[Leaf]:
    return Leaf(9)


def build() -> None:
    x: Tree = [Leaf(1), make_leaf()]  # tpyc: error(/container_lit.slot_family/)
    print(isinstance(x, Leaf))


def main() -> None:
    build()


main()
