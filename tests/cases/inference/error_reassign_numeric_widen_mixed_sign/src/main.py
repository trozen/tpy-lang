# Mixed sign same width is an error
from tpy import int32, uint32

def main() -> None:
    x = int32(1)
    x = uint32(2)  # tpyc: error(/holds int32 values and this value is uint32, which have no common type/)
    print(x)

main()
