# Literal[bool] in @overload parameter annotations: parsing, sema validation,
# and codegen for bool literal dispatch (compilation + execution)
from typing import Literal, overload


@overload
def describe(x: Literal[True]) -> str: ...

@overload
def describe(x: Literal[False]) -> str: ...

@overload
def describe(x: bool) -> str: ...

def describe(x: bool) -> str:
    if x:
        return "yes"
    return "no"


def main() -> None:
    print(describe(True))
    print(describe(False))

    # A local only ever bound to True takes the Literal[True] variant, as
    # the literal does
    val = True
    print(describe(val))

    # A local bound to a non-literal bool takes the bool variant
    flag = len("ab") > 1
    print(describe(flag))  # tpyc: ok


main()
