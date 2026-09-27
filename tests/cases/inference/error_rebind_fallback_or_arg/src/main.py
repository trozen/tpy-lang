# Rejects valid (BUGS.md#container-select-argument-unlowered): the overload fills
# both empty dicts of an `or` argument, which then stops at code generation.
from tpy import dispatch


@dispatch
def tot3(d: dict[str, float]) -> float:
    return 1.5 + len(d)


@dispatch
def tot3(s: str) -> str:
    return s


def rebind() -> None:
    y = 0.5
    # Each `or` operand is typed from the overload's dict[str, float].
    y = tot3({} or {})  # tpyc: error(/not yet supported by C\+\+ code generation \(expr\.call:call\.arg_shape\.container\)/)
    print(y)


rebind()
