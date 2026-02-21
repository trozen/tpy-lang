"""Test type inference with nested generic classes."""


class Inner[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value


class Outer[T]:
    inner: T

    def __init__(self, inner: T) -> None:
        self.inner = inner


# Separate lines: Inner[int] explicit, then Outer inferred
inner = Inner[int](42)
outer = Outer(inner)
print(outer.inner.value)

# Inline nested inference: Outer(Inner(42)) -> Outer[Inner[int]]
outer2 = Outer(Inner(42))
print(outer2.inner.value)
