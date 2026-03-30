# Test error when calling a non-callable expression.
from tpy import Int32

def get_value() -> Int32:
    return 42

def main() -> None:
    get_value()(5)  # tpyc: error(/not callable/)

main()
