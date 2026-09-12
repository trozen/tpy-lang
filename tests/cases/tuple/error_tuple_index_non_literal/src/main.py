# Error: tuple index must be a compile-time integer literal
from tpy import int32

def main() -> None:
    t: tuple[int32, str] = (int32(1), "hello")
    i: int32 = int32(0)
    x = t[i]  # tpyc: error(/compile-time integer literal/)

main()
