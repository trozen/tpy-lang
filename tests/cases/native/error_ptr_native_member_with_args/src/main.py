# The Ptr native-member row requires a ZERO-ARG member; an argument-taking
# @native method on a Ptr receiver keeps rejecting.
from tpy import int32, Ptr
from s2 import S2


def pick(s: Ptr[S2]) -> int32:
    return s.pick(1)  # tpyc: error(/method.marker.deref.ptr/)


def main() -> None:
    print(1)


main()
