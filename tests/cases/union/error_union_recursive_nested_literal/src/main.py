# Error: wrong element types in nested recursive union literals
from dataclasses import dataclass
from tpy import Int32

@dataclass
class Leaf:
    value: Int32

type Tree = Leaf | list[Tree]

def main() -> None:
    x: Tree = [Leaf(1), ["hello"]]  # tpyc: error(/incompatible with annotated element type/)

main()
