# Generic Own[T] params use forwarding refs (T&&) with std::forward.
from tpy import Int32, Own


class Box:
    value: Int32


def consume(x: Own[Box]) -> Int32:
    return x.value


def sink[T](x: Own[T]) -> None:
    pass


def wrapper[T](x: Own[T]) -> None:
    sink[T](x)  # std::forward<T>(x) at last use


def main():
    b1 = Box()
    b1.value = 10
    # Concrete Own: std::move at last use
    print(consume(b1))

    b2 = Box()
    b2.value = 20
    # Generic forwarding chain: wrapper -> sink, both T&&
    wrapper[Box](b2)

    b3 = Box()
    b3.value = 30
    # Same but with inferred type argument
    wrapper(b3)

    print("done")
