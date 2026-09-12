# Error: nested generators in dict comprehension
from tpy import int32

def main() -> None:
    d = {x: y for x in range(3) for y in range(3)}  # tpyc: error(/Nested/)

main()
