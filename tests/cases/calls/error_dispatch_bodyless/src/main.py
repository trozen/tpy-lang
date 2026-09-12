# Error: a @dispatch variant is its own implementation, so it needs a body
# (or @native / @cpp_template); a bodyless def is the typing.overload shape.
from tpy import dispatch, int32


@dispatch
def f(x: int32) -> int32: ...  # tpyc: error(/@dispatch 'f' has no body/)


@dispatch
def f(x: str) -> int32:
    return len(x)


def main() -> None:
    print(f(1))


main()
