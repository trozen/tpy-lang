# Error: **kwargs unpacking not supported

def f(a: int, b: int) -> None:
    pass

def main() -> None:
    f(**b)  # tpyc: error(/kwargs unpacking not supported/)

main()
