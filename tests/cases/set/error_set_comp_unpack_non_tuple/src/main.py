# Error: tuple unpack on non-tuple type in set comprehension
from tpy import Int32

def main() -> None:
    items: list[Int32] = [1, 2, 3]
    s = {k for k, v in items}  # tpyc: error(/Cannot unpack non-tuple/)

main()
