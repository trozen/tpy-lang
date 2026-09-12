# Error: tuple index out of range
from tpy import int32

def main() -> None:
    t: tuple[int32, str] = (int32(1), "hello")
    x = t[2]  # tpyc: error(/out of range/)

main()
