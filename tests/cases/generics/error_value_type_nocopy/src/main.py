# Test error: @nocopy class cannot implement ValueType.
from tpy import Int32, ValueType, nocopy


@nocopy
class Bad(ValueType):  # tpyc: error(/cannot implement ValueType/)
    x: Int32


def main() -> None:
    pass

main()
