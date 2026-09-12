"""Test generic functions with multiple type parameters."""
from tpy import int32, Own


class Pair[A, B]:
    first: A
    second: B

    def __init__(self, first: A, second: B) -> None:
        self.first = first
        self.second = second


def swap_pair[A, B](p: Pair[A, B]) -> Own[Pair[B, A]]:
    return Pair[B, A](p.second, p.first)


def create_pair[A, B](a: A, b: B) -> Own[Pair[A, B]]:
    return Pair[A, B](a, b)


# Inference from arguments
p1 = create_pair(10, "hello")
print(p1.first)
print(p1.second)

# Swap pair
p2 = swap_pair(p1)
print(p2.first)
print(p2.second)
