# A tuple unpack into a declared narrower integer slot is refused: the unpack
# assigns each element as it is, with no place for the range check the
# narrowing needs (BUGS.md#unpack-target-narrowing).
from tpy import uint8, int32


def f(p: tuple[int32, int32]) -> None:
    u8: uint8 = uint8(255)
    # element 1 is int32, which the uint8 slot does not hold every value of
    a, u8 = p  # tpyc: error(/element 1 is int32, which .u8. \(uint8\) does not hold every value of/)
    print(a, u8)


f((1, 256))
