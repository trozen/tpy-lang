# Cross-width aug-assign where value type is wider than target should error (lossy narrowing)
from tpy import int16, int64

def main() -> None:
    i = int16(2)
    i += int64(3)  # tpyc: error(/Operator '\+=' is not supported between int16 and int64/)

main()
