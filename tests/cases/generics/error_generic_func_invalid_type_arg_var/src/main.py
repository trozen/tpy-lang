"""Test error for variable used as type argument in generic function calls."""


def first[T](items: list[T]) -> T:
    return items[0]


nums = [1, 2, 3]
x = 42
first[x](nums)  # tpyc: error(/Unknown type: x/)
