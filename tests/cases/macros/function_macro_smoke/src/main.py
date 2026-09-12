# A @function_macro runs at compile time on the decorated function: it reads
# the function's params / return / body and emits a diagnostic. The function
# still compiles and runs normally (the macro decorator is stripped).
from tracemod import trace
from tpy import int32


@trace
def add(a: int32, b: int32) -> int32:  # tpyc: warning(/function macro saw add\(a, b\) -> int32/)
    return a + b


def main() -> None:
    print(add(2, 3))


main()
