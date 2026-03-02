# Wrong key type in dict subscript should error
from tpy import Int32

def main() -> None:
    d = {"x": 1}
    print(d[Int32(5)])  # tpyc: error(/expected str/)

main()
