"""Test error for uppercase generic function with invalid type arg."""


def First[T](items: list[T]) -> T:  # tpyc: ok
    return items[0]


nums = [1, 2, 3]  # tpyc: ok
First[123](nums)  # tpyc: error(/Integer '123' is not a valid type argument/)
