from a import foo  # tpyc: error(/Circular import detected/)

def bar() -> int:
    return foo()
