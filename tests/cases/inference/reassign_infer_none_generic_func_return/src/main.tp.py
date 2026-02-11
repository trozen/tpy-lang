from tpy import Int32


def first[T](items: list[T]) -> T:
    return items[0]


x = None
x = first([Int32(41), Int32(42)])
print(x)
