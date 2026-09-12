# float64 as a true type alias: star import and constructor calls
from tpy import *

def test_star_import() -> None:
    x: float64 = 3.14
    y = float64(2.0)
    print(x + y)
    # Constructor from fixed-width int types
    print(float64(int32(42)))
    print(float64(int64(100)))
    print(float64(uint32(7)))

def main() -> None:
    test_star_import()

main()
