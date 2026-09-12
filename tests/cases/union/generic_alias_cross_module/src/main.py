# Generic alias defined in one module, used in another with concrete
# type args. Covers both short-name import and qualified module access.
from tpy import int32
import lib
from lib import Pair


def main() -> None:
    p1: Pair[int32] = (int32(1), int32(2))  # tpyc: type(/tuple\[int32, int32\]/)
    p2: lib.Pair[int32] = (int32(3), int32(4))  # tpyc: type(/tuple\[int32, int32\]/)
    print(p1)
    print(p2)


main()
