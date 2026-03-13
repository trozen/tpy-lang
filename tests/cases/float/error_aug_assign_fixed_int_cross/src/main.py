# Cross-width aug-assign where value type is wider than target should error (lossy narrowing)
from tpy import Int16, Int64

def main() -> None:
    i = Int16(2)
    i += Int64(3)  # tpyc: error(/Operator '\+=' is not supported between Int16 and Int64/)

main()
