# Test error: generic function ref with str param (C++ param type mismatch)
from tpy import Fn, Int32

def identity[T](x: T) -> T:
    return x

def apply_str(f: Fn[[str], str], s: str) -> str:
    return f(s)

def main() -> None:
    apply_str(identity, "hello")  # tpyc: error(/different.*parameter convention/)

main()
