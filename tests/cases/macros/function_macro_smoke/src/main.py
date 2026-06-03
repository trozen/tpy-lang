# A @function_macro runs at compile time on the decorated function: it reads
# the function's params / return / body and emits a diagnostic. The function
# still compiles and runs normally (the macro decorator is stripped).
from tracemod import trace
from tpy import Int32


@trace
def add(a: Int32, b: Int32) -> Int32:  # tpyc: warning(/function macro saw add\(a, b\) -> Int32/)
    return a + b


def main() -> None:
    print(add(2, 3))


main()
