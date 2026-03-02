# Error: tuple index must be a compile-time integer literal
from tpy import Int32

def main() -> None:
    t: tuple[Int32, str] = (Int32(1), "hello")
    i: Int32 = Int32(0)
    x = t[i]  # tpyc: error(/compile-time integer literal/)

main()
