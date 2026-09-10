# @dispatch variants: each carries its own body, no trailing impl
from tpy import dispatch


@dispatch
def describe(x: int) -> str:  # tpyc: ok
    return "int: " + str(x)

@dispatch
def describe(x: str) -> str:  # tpyc: ok
    return "str: " + x


def main() -> None:
    print(describe(42))
    print(describe("hello"))


main()
