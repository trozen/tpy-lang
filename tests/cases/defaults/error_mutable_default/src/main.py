# Error: a mutable list-literal default is rejected at parse time.
# Guards against reintroducing CPython's shared-mutable-default gotcha.
def f(a: list[int] = []) -> int:  # tpyc: error(/Default parameter value must be a constant expression/)
    a.append(1)
    return len(a)
