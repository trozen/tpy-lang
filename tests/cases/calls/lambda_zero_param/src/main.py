# Lambda with zero parameters.
from tpy import Fn, int32

def invoke(f: Fn[[], int32]) -> int32:
    return f()

def main() -> None:
    print(invoke(lambda: 42))
    print(invoke(lambda: 0))

main()
