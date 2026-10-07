# The winning overload decides the local, so a later wider value is refused as
# after a call to that one function (LANGUAGE_FEATURES "List Literal Inference").
from tpy import dispatch, int32, int64


def a64() -> int64:
    return 1099511627776


@dispatch
def f(xs: list[int32]) -> str:
    return "list[int32]"


@dispatch
def f(xs: str) -> str:
    return "str"


def main() -> None:
    ms = [1, 2]
    print(f(ms))
    ms.append(a64())  # tpyc: error(/holds int32 elements since line 22 \(passed as list\[int32\]\)/)


main()
