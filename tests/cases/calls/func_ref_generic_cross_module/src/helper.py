# Generic functions for cross-module function reference tests

def identity[T](x: T) -> T:
    return x

def swap[T, U](a: T, b: U) -> tuple[U, T]:
    return (b, a)
