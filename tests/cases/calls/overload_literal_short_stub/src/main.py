# A LITERAL-only @overload group with a SHORT stub: the emitted signature is the
# impl's (defaults included), so the omitted trailing params need no prologue and
# the stub's Literal facts pair with the impl's params as a prefix.
from typing import overload, Literal
from tpy import int32


@overload
def h(a: Literal["x"]) -> int32: ...  # tpyc: ok

@overload
def h(a: str, b: int32) -> int32: ...  # tpyc: ok

def h(a: str, b: int32 = 0) -> int32:
    # The literal stub folds this compare; the wide stub keeps it at runtime.
    if a == "x":
        return b + 100
    return b


def main() -> None:
    print(h("x"), h("y", 2))


main()
