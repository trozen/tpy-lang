# Rejects valid (BUGS.md#container-select-argument-unlowered): the overload fills
# both empty dicts of a ternary argument, which then stops at code generation.
from tpy import dispatch


@dispatch
def tot3(d: dict[str, float]) -> float:
    return 1.5 + len(d)


@dispatch
def tot3(s: str) -> str:
    return s


def rebind(c: bool) -> None:
    y = 0.5
    y = tot3({} if c else {})  # tpyc: error(/not yet supported by C\+\+ code generation \(expr\.call:call\.arg_shape\.container\)/)
    print(y)


rebind(True)
