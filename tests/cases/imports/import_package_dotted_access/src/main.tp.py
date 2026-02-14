from tpy import Int32
import mypackage.utils

def main() -> Int32:
    print(mypackage.utils.add(Int32(3), Int32(4)))
    print(mypackage.utils.add(Int32(5), Int32(6)))
    return Int32(0)

main()
