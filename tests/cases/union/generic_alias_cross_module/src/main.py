# Generic alias defined in one module, used in another with concrete
# type args. Covers both short-name import and qualified module access.
from tpy import Int32
import lib
from lib import Pair


def main() -> None:
    p1: Pair[Int32] = (Int32(1), Int32(2))  # tpyc: type(/tuple\[Int32, Int32\]/)
    p2: lib.Pair[Int32] = (Int32(3), Int32(4))  # tpyc: type(/tuple\[Int32, Int32\]/)
    print(p1)
    print(p2)


main()
