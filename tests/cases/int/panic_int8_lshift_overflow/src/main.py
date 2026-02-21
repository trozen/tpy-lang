# Int8(1) << Int8(7) should panic — would produce 128 which overflows Int8
from tpy import Int8

def main() -> None:
    x: Int8 = Int8(1) << Int8(7)
    print(x)

main()
