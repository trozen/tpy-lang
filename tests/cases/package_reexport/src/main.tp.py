from tpy import Int32
from mypackage import VERSION, add

def main() -> Int32:
    print(VERSION)
    result: Int32 = add(Int32(5), Int32(7))
    print(result)
    return Int32(0)

main()
