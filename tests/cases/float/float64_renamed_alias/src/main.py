# float64 alias with renaming: from tpy import float64 as F64
from tpy import float64 as F64, int32

def main() -> None:
    x: F64 = 1.5
    y = F64(int32(10))
    print(x)
    print(y)

main()
