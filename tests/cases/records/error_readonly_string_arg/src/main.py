# @readonly("x") with string argument is rejected.
@readonly("x")  # tpyc: error(/@readonly\(\) requires a single bool argument/)
def bad() -> int:
    return 0

print(0)
