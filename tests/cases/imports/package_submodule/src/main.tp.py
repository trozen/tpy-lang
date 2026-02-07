from tpy import Int32
from mypackage.utils import add

def main() -> Int32:
    result: Int32 = add(Int32(10), Int32(32))
    print(result)
    return Int32(0)

main()
