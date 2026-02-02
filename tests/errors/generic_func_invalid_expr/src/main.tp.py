"""Test error for expression used as type argument."""


def first[T](items: list[T]) -> T:  # tpyc: ok
    return items[0]


nums = [1, 2, 3]  # tpyc: ok
first[int + str](nums)  # tpyc: error(/Cannot parse type annotation/)
