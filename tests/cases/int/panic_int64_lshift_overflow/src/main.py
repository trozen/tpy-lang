# int64(1) << int64(63) should panic — would produce INT64_MIN-equivalent overflow
from tpy import int64

def main() -> None:
    x: int64 = int64(1) << int64(63)
    print(x)

main()
