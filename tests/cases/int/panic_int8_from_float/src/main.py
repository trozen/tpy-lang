# Int8(128.0) should panic — just above max
from tpy import Int8

def main() -> None:
    x: Int8 = Int8(128.0)
    print(x)

main()
