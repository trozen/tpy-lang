# A readonly name narrowed by isinstance stays readonly at the parameter of a
# dunder only inferred const (in an imported module, so inferred before this
# call is analyzed): the operator form refuses it as a plain call would.
from tpy import readonly

from holder import Holder, Item


def f(v: readonly[Item | str], h: Holder) -> None:
    if isinstance(v, Item):
        print("op:", v & h)  # tpyc: error(/Cannot pass readonly\[Item\] as mutable Item/)


def main() -> None:
    f(Item(2), Holder())


main()
