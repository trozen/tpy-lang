# Test error: @nocopy class cannot implement ValueType.
from tpy import int32, ValueType, nocopy


@nocopy
class Bad(ValueType):  # tpyc: error(/cannot implement ValueType/)
    x: int32


def main() -> None:
    pass

main()
