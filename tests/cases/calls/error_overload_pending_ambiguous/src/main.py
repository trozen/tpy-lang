# Two candidates take this empty list local at no widening, and a tie is an
# error (LANGUAGE_FEATURES "List Literal Inference"): neither may decide it.
from tpy import dispatch, int64


@dispatch
def f(xs: list[str]) -> str:
    return "str"


@dispatch
def f(xs: list[int64]) -> str:
    return "int64"


def main() -> None:
    xs = []
    print(f(xs))  # tpyc: error(/Ambiguous overload for .f.: multiple candidates match equally: f\(list\[str\]\); f\(list\[int64\]\)/)


main()
