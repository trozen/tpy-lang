# Test uint8 overflow panics
from tpy import uint8

def main() -> None:
    a: uint8 = uint8(255)
    b: uint8 = a + uint8(1)
    print(b)

main()
