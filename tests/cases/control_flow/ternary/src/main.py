# Ternary expression: basic same-type usage, nesting, various contexts
from tpy import Int32

def abs_val(x: Int32) -> Int32:
    return x if x >= 0 else -x

def max_val(a: Int32, b: Int32) -> Int32:
    return a if a > b else b

def clamp(x: Int32, lo: Int32, hi: Int32) -> Int32:
    # Nested ternary
    return lo if x < lo else (hi if x > hi else x)

def greet(formal: bool) -> str:
    return "Good day" if formal else "Hey"

def describe(x: Int32) -> str:
    label: str = "positive" if x > 0 else "non-positive"
    return label

def main() -> None:
    # Basic ternary
    print(abs_val(5))
    print(abs_val(-3))

    # Ternary as function return
    print(max_val(10, 20))
    print(max_val(30, 15))

    # Nested ternary
    print(clamp(-5, 0, 10))
    print(clamp(5, 0, 10))
    print(clamp(15, 0, 10))

    # Ternary with strings
    print(greet(True))
    print(greet(False))

    # Ternary in assignment
    print(describe(1))
    print(describe(-1))

    # Ternary as function argument
    x: Int32 = 7
    print(x if x > 5 else 0)

    # Ternary with bool condition variable
    flag: bool = True
    val: Int32 = 100 if flag else 200
    print(val)

main()
