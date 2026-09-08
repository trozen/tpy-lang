"""Test error for expression used as type argument."""


def first[T](items: list[T]) -> T:
    return items[0]


nums = [1, 2, 3]
first[int + str](nums)  # tpyc: error(/Cannot parse type annotation/)
