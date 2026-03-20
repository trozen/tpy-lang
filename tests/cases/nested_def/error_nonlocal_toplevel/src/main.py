# Test error: nonlocal outside nested def
from tpy import Int32

x: Int32 = 10

def main() -> None:
    nonlocal x  # tpyc: error(/nonlocal.*only valid inside a nested function/)

main()
