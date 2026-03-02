# Error: tuple index out of range
from tpy import Int32

def main() -> None:
    t: tuple[Int32, str] = (Int32(1), "hello")
    x = t[2]  # tpyc: error(/out of range/)

main()
