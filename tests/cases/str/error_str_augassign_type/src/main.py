# str += non-string is a type error
from tpy import int32

def main() -> None:
    s: str = "hello"
    s += int32(42)  # tpyc: error(/string type/)

main()
