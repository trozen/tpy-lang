# Float64 as a true type alias: star import and constructor calls
from tpy import *

def test_star_import() -> None:
    x: Float64 = 3.14
    y = Float64(2.0)
    print(x + y)
    # Constructor from fixed-width int types
    print(Float64(Int32(42)))
    print(Float64(Int64(100)))
    print(Float64(UInt32(7)))

def main() -> None:
    test_star_import()

main()
