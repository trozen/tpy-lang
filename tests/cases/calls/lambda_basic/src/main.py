# Lambda expressions with Fn-typed parameters: basic type inference and codegen.
from tpy import Fn, Int32

def apply(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def main() -> None:
    print(apply(lambda x: x + 1, 10))
    print(apply(lambda x: x * 2, 5))
    print(apply(lambda x: x, 0))

main()
