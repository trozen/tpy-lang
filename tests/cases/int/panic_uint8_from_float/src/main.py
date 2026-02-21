# UInt8(256.0) should panic — just above max
from tpy import UInt8

def main() -> None:
    x: UInt8 = UInt8(256.0)
    print(x)

main()
