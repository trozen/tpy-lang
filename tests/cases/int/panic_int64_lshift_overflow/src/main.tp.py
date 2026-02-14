# Int64(1) << Int64(63) should panic — would produce INT64_MIN-equivalent overflow
from tpy import Int64

def main() -> None:
    x: Int64 = Int64(1) << Int64(63)
    print(x)

main()
