# round() panics when rounding causes fixed-width integer overflow
from tpy import int32

def main() -> None:
    x: int32 = int32(2147483647)
    print(round(x, int32(-1)))

main()
