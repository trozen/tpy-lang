"""Test error when unknown type used as explicit type argument."""


def first[T](items: list[T]) -> T:
    return items[0]


nums = [1, 2, 3]
first[UnknownType](nums)  # tpyc: error(/Unknown type/)
