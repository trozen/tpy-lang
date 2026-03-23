# Float64 alias with renaming: from tpy import Float64 as F64
from tpy import Float64 as F64, Int32

def main() -> None:
    x: F64 = 1.5
    y = F64(Int32(10))
    print(x)
    print(y)

main()
