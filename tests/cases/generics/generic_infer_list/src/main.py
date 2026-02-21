"""Test type inference with list argument."""


class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value


# Inference from list literal -> Box[list[int]]
box = Box([1, 2, 3])
print(box.value[0])
print(len(box.value))
