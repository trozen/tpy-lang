# *args without type annotation should error
def f(*args) -> None:  # tpyc: error(/type annotation/)
    pass

f(1, 2, 3)
