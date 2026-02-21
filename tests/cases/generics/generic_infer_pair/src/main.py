"""Test type inference with multiple type parameters."""


class Pair[A, B]:
    first: A
    second: B

    def __init__(self, first: A, second: B) -> None:
        self.first = first
        self.second = second


# Inference from int, str -> Pair[int, str]
pair = Pair(1, "hello")
print(pair.first)
print(pair.second)
