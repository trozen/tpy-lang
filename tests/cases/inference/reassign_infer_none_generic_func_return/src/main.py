from tpy import int32


def first[T](items: list[T]) -> T:
    return items[0]


x = None
x = first([int32(41), int32(42)])
print(x)
