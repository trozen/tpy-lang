# pow() panics on negative exponent for fixed-width integers
from tpy import int32

def main() -> None:
    a: int32 = int32(2)
    b: int32 = int32(-1)
    print(pow(a, b))

main()
