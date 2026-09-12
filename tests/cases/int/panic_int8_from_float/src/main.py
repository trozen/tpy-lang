# int8(128.0) should panic — just above max
from tpy import int8

def main() -> None:
    x: int8 = int8(128.0)
    print(x)

main()
