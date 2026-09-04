# A LITERAL-only @overload group with a SHORT stub: the emitted signature is the
# impl's (defaults included), so the omitted trailing params need no prologue and
# the stub's Literal facts pair with the impl's params as a prefix.
from typing import overload, Literal
from tpy import Int32


@overload
def h(a: Literal["x"]) -> Int32: ...  # tpyc: ok

@overload
def h(a: str, b: Int32) -> Int32: ...  # tpyc: ok

def h(a: str, b: Int32 = 0) -> Int32:
    # The literal stub folds this compare; the wide stub keeps it at runtime.
    if a == "x":
        return b + 100
    return b


def main() -> None:
    print(h("x"), h("y", 2))


main()
