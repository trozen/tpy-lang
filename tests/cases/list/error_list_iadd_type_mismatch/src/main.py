# Error: list += with mismatched element type
from tpy import int32

def main() -> None:
    a: list[int32] = [1, 2]
    b: list[str] = ["x"]
    a += b  # tpyc: error(/does not conform to protocol Iterable/)

main()
