# Error: v1 does not walk MRO for class constants -- `Child.X` only resolves
# when X is declared on Child. MRO walk lands in Phase 6.
from typing import Final
from tpy import Int32


class Parent:
    LIMIT: Final[Int32] = 10


class Child(Parent):
    pass


def main() -> None:
    print(Child.LIMIT)  # tpyc: error(/no class constant 'LIMIT' on 'Child'; declared on 'Parent' \(use 'Parent.LIMIT'\)/)


main()
