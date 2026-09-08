# Literal[int] in @overload parameter annotations: parsing, sema validation,
# and codegen for integer literal dispatch (compilation + execution)
from typing import Literal, overload
from tpy import Int32


@overload
def classify(x: Literal[1, 2]) -> str: ...

@overload
def classify(x: Literal[3, 4]) -> str: ...

@overload
def classify(x: Literal[-1, -2]) -> str: ...

@overload
def classify(x: Int32) -> str: ...

def classify(x: Int32) -> str:
    if x == 1:
        return "one"
    elif x == 2:
        return "two"
    elif x == 3:
        return "three"
    elif x == 4:
        return "four"
    elif x == -1:
        return "neg_one"
    elif x == -2:
        return "neg_two"
    return "other"


# A literal-only group with NO base-type fallback stub: an accepted call must
# land on one of the Literal stubs, and the per-stub return types make that
# selection observable. The call whose literal matches no stub is the reject
# pinned by tests/cases/calls/error_overload_literal_no_match.

@overload
def only_lit(x: Literal[1]) -> Int32: ...

@overload
def only_lit(x: Literal[2]) -> str: ...

def only_lit(x: Int32) -> Int32 | str:
    if x == 1:
        return 42
    return "two"


def main() -> None:
    # Direct int literals match Literal stubs
    print(classify(1))
    print(classify(2))
    print(classify(3))
    print(classify(4))

    # Negative int literals
    print(classify(-1))
    print(classify(-2))

    # Variable falls through to Int32 fallback
    x: Int32 = 5
    print(classify(x))

    # No fallback stub: each literal still resolves to its own stub.
    print("only_lit:", only_lit(1) + 1, only_lit(2) + "!")


main()
