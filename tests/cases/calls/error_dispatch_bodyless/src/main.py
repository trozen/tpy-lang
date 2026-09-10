# Error: a @dispatch variant is its own implementation, so it needs a body
# (or @native / @cpp_template); a bodyless def is the typing.overload shape.
from tpy import dispatch, Int32


@dispatch
def f(x: Int32) -> Int32: ...  # tpyc: error(/@dispatch 'f' has no body/)


@dispatch
def f(x: str) -> Int32:
    return len(x)


def main() -> None:
    print(f(1))


main()
