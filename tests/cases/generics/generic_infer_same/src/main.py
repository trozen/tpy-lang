"""Test type inference with same type param used twice."""


class Same[T]:
    a: T
    b: T

    def __init__(self, a: T, b: T) -> None:
        self.a = a
        self.b = b


# Both args are int -> Same[int]
same = Same(1, 2)
print(same.a)
print(same.b)
