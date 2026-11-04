# A `return` only in a dead arm returns nothing: the function still falls off
# its end, and the missing return is reported.
def f() -> int:  # tpyc: error(/'f' can reach the end of the function without returning a value/)
    if False:
        return 3


print(f())
