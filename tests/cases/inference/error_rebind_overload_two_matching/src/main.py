# An empty dict literal two overloads both accept at no widening is an
# ambiguous call: neither candidate's parameter may decide it.
from tpy import dispatch


@dispatch
def amb(d: dict[str, float]) -> float:
    return 1.5


@dispatch
def amb(d: dict[int, float]) -> float:
    return 2.5


def rebind() -> None:
    y = 0.5
    y = amb({})  # tpyc: error(/Ambiguous overload for .amb./)
    print(y)


rebind()
