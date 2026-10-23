# Wrong key type in dict subscript should error
from tpy import int32

def main() -> None:
    d = {"x": 1}
    print(d[int32(5)])  # tpyc: error(/Type mismatch in dict key: expected str, got int32/)

main()
