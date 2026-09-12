from tpy import int32
import mypackage.utils

def main() -> int32:
    print(mypackage.utils.add(int32(3), int32(4)))
    print(mypackage.utils.add(int32(5), int32(6)))
    return int32(0)

main()
