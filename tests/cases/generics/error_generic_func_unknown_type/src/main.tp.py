"""Test error when unknown type used as explicit type argument."""


def first[T](items: list[T]) -> T:  # tpyc: ok
    return items[0]


nums = [1, 2, 3]  # tpyc: ok
first[UnknownType](nums)  # tpyc: error(/Unknown type/)
