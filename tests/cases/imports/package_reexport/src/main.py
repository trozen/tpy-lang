from tpy import int32
from mypackage import VERSION, add

def main() -> int32:
    print(VERSION)
    result: int32 = add(int32(5), int32(7))
    print(result)
    return int32(0)

main()
