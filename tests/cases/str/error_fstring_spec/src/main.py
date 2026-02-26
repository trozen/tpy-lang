# Error: unsupported f-string format spec features.
from tpy import Int32


def test_comma() -> None:
    x: Int32 = Int32(1234567)
    s: str = f"{x:,}"  # tpyc: error(/grouping is not supported/)
    print(s)

test_comma()
