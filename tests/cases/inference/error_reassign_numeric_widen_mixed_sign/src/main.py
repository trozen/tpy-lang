# Mixed sign same width is an error
from tpy import Int32, UInt32

def main() -> None:
    x = Int32(1)
    x = UInt32(2)  # tpyc: error(/Type mismatch/)
    print(x)

main()
