# UInt64 cannot widen to Int64 (same width, mixed sign)
from tpy import UInt64, Int64

def main() -> None:
    x = UInt64(1)
    x = Int64(2)  # tpyc: error(/Type mismatch/)
    print(x)

main()
