from tpy import int32
from mypackage.utils import add

def main() -> int32:
    result: int32 = add(int32(10), int32(32))
    print(result)
    return int32(0)

main()
