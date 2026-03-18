# Lambda parameter count must match Fn signature.
from tpy import Fn, Int32

def apply(f: Fn[[Int32, Int32], Int32], x: Int32) -> Int32:
    return f(x, x)

def main() -> None:
    apply(lambda x: x + 1, 5)  # tpyc: error(/Lambda has 1 parameter.*expects 2/)

main()
