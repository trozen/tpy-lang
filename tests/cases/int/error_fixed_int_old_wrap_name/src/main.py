# uint32.add_wrap is not an attribute of uint32: the wrapping addition is
# spelled uint32.wrap_add.
from tpy import uint32


def main(a: uint32, b: uint32) -> None:
    print(uint32.add_wrap(a, b))  # tpyc: error(/class 'uint32' has no attribute 'add_wrap'/)


main(1, 2)
