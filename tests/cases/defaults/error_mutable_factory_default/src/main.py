# Error: a container-factory call (list()) is not a constant default value.
def f(a: list[int] = list()) -> int:  # tpyc: error(/Default parameter value must be a constant expression/)
    return len(a)
