# pow() panics on negative exponent for fixed-width integers
from tpy import Int32

def main() -> None:
    a: Int32 = Int32(2)
    b: Int32 = Int32(-1)
    print(pow(a, b))

main()
