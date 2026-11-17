# A pointer to a by-value parameter points at the function's own copy
# (docs/LANGUAGE_FEATURES.md: a parameter the function holds its own copy of).
from tpy import Ptr, int32, take_ptr


def addr(n: int32) -> Ptr[int32]:
    return take_ptr(n)  # tpyc: error(/Cannot return pointer to parameter 'n' \(the function's own copy\); the returned pointer would dangle/)


def main() -> None:
    print(addr(3) is None)


main()
