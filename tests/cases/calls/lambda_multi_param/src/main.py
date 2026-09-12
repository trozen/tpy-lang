# Lambda with multiple parameters via Fn type.
from tpy import Fn, int32

def combine(f: Fn[[int32, int32], int32], a: int32, b: int32) -> int32:
    return f(a, b)

def main() -> None:
    print(combine(lambda x, y: x + y, 3, 4))
    print(combine(lambda x, y: x * y, 3, 4))
    print(combine(lambda x, y: x - y, 10, 3))

main()
