# Using a type param as explicit type arg in a non-generic function still errors.
from tpy import Int32


def identity[T](x: T) -> T:
    return x


def not_generic(x: Int32) -> Int32:
    return identity[T](x)  # tpyc: error(/Unknown type: T/)


def main():
    print(not_generic(1))
