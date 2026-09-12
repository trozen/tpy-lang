# uint64 cannot widen to int64 (same width, mixed sign)
from tpy import uint64, int64

def main() -> None:
    x = uint64(1)
    x = int64(2)  # tpyc: error(/Type mismatch/)
    print(x)

main()
