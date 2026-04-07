# Error: **kwargs unpacking on function without **kwargs

def f(a: int, b: int) -> None:
    pass

def main() -> None:
    f(**b)  # tpyc: error(/does not accept \*\*kwargs/)

main()
