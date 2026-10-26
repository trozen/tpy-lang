# A generic default must fit the instantiated type (LANGUAGE_FEATURES "Default
# parameter values"), also when a later use completes `Bag()` as `Bag[str]`.
from tpy import copy


class Bag[T]:
    v: T

    def __init__(self, v: T = 0) -> None:
        self.v = copy(v)

    def put(self, v: T) -> None:
        self.v = copy(v)


def main() -> None:
    b = Bag()  # tpyc: error(/Default value for 'v' is incompatible with type 'str'/)
    b.put("x")
    print(b.v)


main()
