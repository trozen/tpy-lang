# Rejects valid (BUGS.md#overload-args-typed-before-candidate): the overload
# fills the float32 width but converts no int, so {"k": 1} matches nothing.
from tpy import dispatch, float32


@dispatch
def f32d(d: dict[str, float32]) -> float32:
    return d["k"]


@dispatch
def f32d(s: str) -> str:
    return s


def rebind() -> None:
    z = float32(0.5)
    z = f32d({"k": 1})  # tpyc: error(/No matching overload for f32d\(dict\[str, int32\]\)/)
    print(z)


rebind()
