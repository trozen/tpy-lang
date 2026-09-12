# uint8(256.0) should panic — just above max
from tpy import uint8

def main() -> None:
    x: uint8 = uint8(256.0)
    print(x)

main()
