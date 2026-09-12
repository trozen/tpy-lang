# Error: tuple unpack on non-tuple type in dict comprehension
from tpy import int32

def main() -> None:
    items: list[int32] = [1, 2, 3]
    d = {k: v for k, v in items}  # tpyc: error(/Cannot unpack non-tuple/)

main()
