# Lambda with zero parameters.
from tpy import Fn, Int32

def invoke(f: Fn[[], Int32]) -> Int32:
    return f()

def main() -> None:
    print(invoke(lambda: 42))
    print(invoke(lambda: 0))

main()
