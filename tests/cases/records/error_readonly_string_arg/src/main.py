# @readonly("x") with string argument is rejected.
from tpy import readonly

@readonly("x")  # tpyc: error(/@readonly\(\) requires a bool argument/)
def bad() -> int:
    return 0

print(0)
