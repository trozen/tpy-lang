# When the receiver of `obj.CLASS_CONST` has side effects (a function call,
# subscript, etc.), codegen wraps the access in a GCC statement expression so
# the receiver is evaluated for its effects and the constant is the value.
from typing import Final
from tpy import int32, Own


class C:
    LIMIT: Final[int32] = 7

    def __init__(self) -> None:
        pass


def make_c() -> Own[C]:
    print("side_effect")
    return C()


def main() -> None:
    print(make_c().LIMIT)
    cs: list[C] = [C(), C()]
    # Subscript also has side effects (range check), so it must be evaluated.
    print(cs[0].LIMIT)


main()
