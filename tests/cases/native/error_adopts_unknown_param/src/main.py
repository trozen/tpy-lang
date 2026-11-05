# `adopts=(...)` names the parameters a binding's result takes over; a name
# that is not a parameter of the binding is an error, not silently dropped.
from typing import Iterable, Iterator
from tpy import int32
from tpy.extern import cpp_template


@cpp_template("::tpy::builtin_enumerate({0})", adopts=("items",))
def numbered(iterable: Iterable[int32]) -> Iterator[tuple[int32, int32]]: ...  # tpyc: error(/adopts= names 'items', which is not a parameter of 'numbered'/)


def main() -> None:
    print(list(numbered([1, 2])))


main()
