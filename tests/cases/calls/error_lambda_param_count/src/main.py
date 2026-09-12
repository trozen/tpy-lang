# Lambda parameter count must match Fn signature.
from tpy import Fn, int32

def apply(f: Fn[[int32, int32], int32], x: int32) -> int32:
    return f(x, x)

def main() -> None:
    apply(lambda x: x + 1, 5)  # tpyc: error(/Lambda has 1 parameter.*expects 2/)

main()
