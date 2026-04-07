# del on function parameter
def f(x: int) -> None:
    print(x)
    del x
    x = 99
    print(x)

f(42)
