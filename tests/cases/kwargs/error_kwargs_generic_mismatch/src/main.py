# Error: kwarg values have conflicting types for generic inference

def first[T](a: T, b: T) -> T:
    return a

def main() -> None:
    first(a=1, b="hello")  # tpyc: error(/Cannot infer type arguments/)

main()
