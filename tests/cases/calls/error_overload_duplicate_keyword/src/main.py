# A keyword naming a parameter a positional already fills, at an overload
# set: no candidate binds the call, all for that one reason, which is
# reported.
from tpy import dispatch, int32


@dispatch
def g(x: int32) -> str:
    return "int"


@dispatch
def g(x: str) -> str:
    return "str"


def main() -> None:
    print(g(1, x=2))  # tpyc: error(/'g' got multiple values for argument 'x'/)


main()
