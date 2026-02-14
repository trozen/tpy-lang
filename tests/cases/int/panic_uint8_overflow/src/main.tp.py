# Test UInt8 overflow panics
from tpy import UInt8

def main() -> None:
    a: UInt8 = UInt8(255)
    b: UInt8 = a + UInt8(1)
    print(b)

main()
