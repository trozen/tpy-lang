"""Test error for invalid type arguments in generic function calls."""


def first[T](items: list[T]) -> T:  # tpyc: ok
    return items[0]


nums = [1, 2, 3]  # tpyc: ok
first[123](nums)  # tpyc: error(/Integer '123' is not a valid type argument/)
