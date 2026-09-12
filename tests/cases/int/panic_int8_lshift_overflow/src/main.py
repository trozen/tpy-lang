# int8(1) << int8(7) should panic — would produce 128 which overflows int8
from tpy import int8

def main() -> None:
    x: int8 = int8(1) << int8(7)
    print(x)

main()
