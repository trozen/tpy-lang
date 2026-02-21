"""Test that inference fails when same type param gets different types."""


class Same[T]:
    a: T
    b: T

    def __init__(self, a: T, b: T) -> None:
        self.a = a
        self.b = b


# T can't be both int and str - should fail
same = Same(1, "hi")  # tpyc: error(/Cannot infer type arguments/)
