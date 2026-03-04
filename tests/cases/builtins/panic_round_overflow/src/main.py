# round() panics when rounding causes fixed-width integer overflow
from tpy import Int32

def main() -> None:
    x: Int32 = Int32(2147483647)
    print(round(x, Int32(-1)))

main()
