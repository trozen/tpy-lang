# Lambda expressions with Fn-typed parameters: basic type inference and codegen.
from tpy import Fn, int32

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

def main() -> None:
    print(apply(lambda x: x + 1, 10))
    print(apply(lambda x: x * 2, 5))
    print(apply(lambda x: x, 0))

main()
