# Rejects valid (BUGS.md#overload-args-typed-before-candidate): an overload
# argument mixing an int and a float is refused, as in a fresh binding.
from tpy import dispatch, int32


@dispatch
def twice(v: float) -> float:
    return v * 2.0


@dispatch
def twice(v: str) -> str:
    return v


def rebind(a: int32, c: bool) -> None:
    y = 0.5
    y = twice(a if c else 2.5)  # tpyc: error(/this conditional expression mixes int32 and float.*float\(a\) if c else 2\.5/)
    print(y)


rebind(3, True)
