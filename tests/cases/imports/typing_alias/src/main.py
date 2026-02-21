# Test typing imports with aliases (from typing import Optional as Opt)
from typing import Optional as Opt
from tpy import Int32

def maybe_double(x: Opt[Int32]) -> Int32:
    if x is not None:
        return x * Int32(2)
    return Int32(0)

def main():
    print(maybe_double(Int32(5)))
    print(maybe_double(None))

main()
