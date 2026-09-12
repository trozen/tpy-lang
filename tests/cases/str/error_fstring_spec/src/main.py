# Error: unsupported f-string format spec features.
from tpy import int32


def test_comma() -> None:
    x: int32 = int32(1234567)
    s: str = f"{x:,}"  # tpyc: error(/grouping is not supported/)
    print(s)

test_comma()
