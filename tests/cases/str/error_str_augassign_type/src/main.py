# str += non-string is a type error
from tpy import Int32

def main() -> None:
    s: str = "hello"
    s += Int32(42)  # tpyc: error(/string type/)

main()
