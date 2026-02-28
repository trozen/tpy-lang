# Test error: ValueType record with non-value-type field.
from tpy import Int32, ValueType


class Inner:
    x: Int32


class Bad(ValueType):
    val: Int32
    inner: Inner  # tpyc: error(/non-value type 'Inner'/)


def main() -> None:
    pass

main()
