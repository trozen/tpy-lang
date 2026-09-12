# Forwarding type params as explicit type args to other generic functions.
from tpy import int32, Own


class Box:
    value: int32


def sink[T](x: Own[T]) -> None:
    pass


def identity[T](x: T) -> T:
    return x


# Forward T as explicit type arg
def wrapper[T](x: Own[T]) -> None:
    sink[T](x)  # tpyc: ok


# Forward as compound type arg: list[T]
def wrap_list[T](items: list[T]) -> list[T]:
    return identity[list[T]](items)  # tpyc: ok


# Multi-param forward
def multi[A, B](a: A, b: B) -> A:
    return identity[A](a)  # tpyc: ok


# Forward record type param from a generic class method
class Container[T]:
    val: T

    def forward_val(self) -> T:
        return identity[T](self.val)  # tpyc: ok


def main():
    b = Box()
    b.value = 42
    wrapper[Box](b)

    nums = [1, 2, 3]
    result = wrap_list[int32](nums)
    print(result)

    print(multi[int32, int32](10, 20))

    print("done")
