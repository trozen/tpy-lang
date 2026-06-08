# A @function_macro reads callee overload signatures via
# ctx.lookup_function_signatures: a module-local function, an imported one
# (cross-module), an alias, a zero-param function, an unknown name (None),
# and an overloaded name (a 2-element list -- overloads are visible, not
# collapsed). It then retypes a bool-string local to the bool param it
# flows into, so the program only compiles if the looked-up types are
# correct and usable.
from typing import overload

from helpermod import paint, Color, paint as painter
from paramsmod import deduce_from_slot


def local_sink(flag: bool, label: str) -> bool:
    return flag if label else flag


@overload
def amb(x: bool) -> bool:
    return x


@overload
def amb(x: str) -> str:
    return x


@deduce_from_slot
def run() -> bool:
    armed = "true"
    return local_sink(amb(armed), "go")


def main() -> None:
    print(1 if run() else 0)
    print(1 if paint(Color.RED) else 0)
    print(1 if amb(True) else 0)
    s = "ok"
    print(amb(s))


main()
