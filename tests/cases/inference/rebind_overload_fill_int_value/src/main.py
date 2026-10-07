# A dict literal written in an overloaded call is scored with its numbers
# still literals: the one candidate that takes a dict wins and its declared
# float32 value converts the int, also under an inferred rebind.
from tpy import dispatch, float32


@dispatch
def f32d(d: dict[str, float32]) -> float32:
    return d["k"]


@dispatch
def f32d(s: str) -> str:
    return s


def rebind() -> None:
    z = float32(0.5)
    # the literal's 1 becomes a float32 at the winner's parameter; compared,
    # not printed: CPython keeps the int and would print 1 for 1.0
    z = f32d({"k": 1})  # tpyc: ok
    print(z == 1.0)


rebind()
