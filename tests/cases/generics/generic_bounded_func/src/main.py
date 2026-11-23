# Test bounded type parameters on a function
# This tests that bounds are validated during type inference
from typing import Sized

def identity[T: Sized](item: T) -> T:
    return item

def main() -> None:
    # list[int] satisfies Sized, so inference should work; the result is the
    # argument itself, so it is a local (a fresh one lives for its statement)
    xs = [1, 2, 3]
    items = identity(xs)
    print(len(items))  # Should print 3

    # str satisfies Sized too
    s = identity("hello")
    print(len(s))  # Should print 5
