# Lambda with multiple parameters via Fn type.
from tpy import Fn, Int32

def combine(f: Fn[[Int32, Int32], Int32], a: Int32, b: Int32) -> Int32:
    return f(a, b)

def main() -> None:
    print(combine(lambda x, y: x + y, 3, 4))
    print(combine(lambda x, y: x * y, 3, 4))
    print(combine(lambda x, y: x - y, 10, 3))

main()
