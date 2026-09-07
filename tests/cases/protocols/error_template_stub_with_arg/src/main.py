# A protocol stub whose template takes an ARGUMENT (`x.__getitem__(1)`) renders
# through the inline-template loop, which the protocol call rows do not mirror.
# TPy rejects that call today.
from typing import Sequence
from tpy import Int32


def pick(x: Sequence[Int32]) -> None:
    print(x.__getitem__(1))  # tpyc: error(/method.fi_kind/)


def main() -> None:
    xs: list[Int32] = [1, 2, 3]
    pick(xs)


main()
