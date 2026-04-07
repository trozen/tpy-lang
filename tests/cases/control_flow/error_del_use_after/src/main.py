# use after del should be rejected
def f() -> None:
    x = 42
    del x
    print(x)  # tpyc: error(/not.*assigned/)
