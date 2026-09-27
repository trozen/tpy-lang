# Two overloads match an inferred float local, so neither fills the empty dict,
# which matches none (BUGS.md#pending-container-overload-no-match).
from tpy import dispatch


@dispatch
def amb(d: dict[str, float]) -> float:
    return 1.5


@dispatch
def amb(d: dict[int, float]) -> float:
    return 2.5


def rebind() -> None:
    y = 0.5
    y = amb({})  # tpyc: error(/No matching overload for amb/)
    print(y)


rebind()
