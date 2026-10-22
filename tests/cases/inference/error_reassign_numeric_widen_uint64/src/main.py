# uint64 cannot widen to int64 (same width, mixed sign)
from tpy import uint64, int64

def main() -> None:
    x = uint64(1)
    x = int64(2)  # tpyc: error(/holds uint64 values and this value is int64, which have no common type/)
    print(x)

main()
