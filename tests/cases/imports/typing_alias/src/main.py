# Test typing imports with aliases (from typing import Optional as Opt)
from typing import Optional as Opt
from tpy import int32

def maybe_double(x: Opt[int32]) -> int32:
    if x is not None:
        return x * int32(2)
    return int32(0)

def main():
    print(maybe_double(int32(5)))
    print(maybe_double(None))

main()
