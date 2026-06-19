# Error: a mutable dict-literal default is rejected at parse time.
def f(a: dict[int, int] = {}) -> int:  # tpyc: error(/Default parameter value must be a constant expression/)
    return len(a)
