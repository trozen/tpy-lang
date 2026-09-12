# Test error: ValueType record with non-value-type field.
from tpy import int32, ValueType


class Inner:
    x: int32


class Bad(ValueType):
    val: int32
    inner: Inner  # tpyc: error(/non-value type 'Inner'/)


def main() -> None:
    pass

main()
