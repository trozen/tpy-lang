"""Test error for variable used as type argument in generic function calls."""


def first[T](items: list[T]) -> T:  # tpyc: ok
    return items[0]


nums = [1, 2, 3]  # tpyc: ok
x = 42  # tpyc: ok
first[x](nums)  # tpyc: error(/Unknown type: x/)
