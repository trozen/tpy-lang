# Mixed sign same width is an error
from tpy import int32, uint32

def main() -> None:
    x = int32(1)
    x = uint32(2)  # tpyc: error(/Type mismatch/)
    print(x)

main()
