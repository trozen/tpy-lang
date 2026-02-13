def foo() -> None:
    global x  # tpyc: error(/not defined at module level/)
    x = 1

foo()
