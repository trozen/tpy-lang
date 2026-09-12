# Test generic function references with type parameter inference from Fn/Callable hints
from typing import Callable
from tpy import Fn, int32, Comparable

def identity[T](x: T) -> T:
    return x

def pair[T, U](a: T, b: U) -> tuple[T, U]:
    return (a, b)

def max_val[T: Comparable](a: T, b: T) -> T:
    if a > b:
        return a
    return b

# Fn param (zero-cost template)
def apply_fn(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

# Callable param (type-erased std::function)
def apply_callable(f: Callable[[int32], int32], x: int32) -> int32:
    return f(x)

# Multi type params
def make_pair(f: Fn[[int32, int32], tuple[int32, int32]], a: int32, b: int32) -> tuple[int32, int32]:
    return f(a, b)

# Bounded generic
def apply2(f: Fn[[int32, int32], int32], a: int32, b: int32) -> int32:
    return f(a, b)

# Callable local variable
def use_local() -> None:
    f: Callable[[int32], int32] = identity
    print(f(99))

# Return as Callable
def get_identity() -> Callable[[int32], int32]:
    return identity

# Void hint -- generic function's return value discarded
def run_void(f: Fn[[int32], None], x: int32) -> None:
    f(x)

def main() -> None:
    # Basic: identity[T] inferred as identity[int32]
    print(apply_fn(identity, 42))          # 42
    print(apply_callable(identity, 42))    # 42

    # Multi type params: pair[T, U] inferred as pair[int32, int32]
    print(make_pair(pair, 3, 7))           # (3, 7)

    # Bounded: max_val[T: Comparable] inferred as max_val[int32]
    print(apply2(max_val, 10, 3))          # 10

    # Callable local variable
    use_local()                             # 99

    # Return as Callable
    f = get_identity()
    print(f(7))                             # 7

    # Void hint with generic function (return discarded)
    run_void(identity, 0)                   # (no output)

main()
