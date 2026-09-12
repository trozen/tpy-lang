# Test error: nonlocal outside nested def
from tpy import int32

x: int32 = 10

def main() -> None:
    nonlocal x  # tpyc: error(/nonlocal.*only valid inside a nested function/)

main()
