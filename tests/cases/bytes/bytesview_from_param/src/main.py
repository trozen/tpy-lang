# BytesView of param data and bytes-literal data are safe to return.
# Complements error_bytesview_dangling_return (negative) with positive
# cases: param-derived and static-literal-derived BytesView returns.
from tpy import BytesView

def from_param(b: bytes) -> BytesView:
    return b

def from_param_sliced(b: bytes) -> BytesView:
    return b[1:]

def from_literal() -> BytesView:
    bv: BytesView = b"hello"
    return bv

def main() -> None:
    data = b"abcdef"
    print(from_param(data).decode())
    print(from_param_sliced(data).decode())
    print(from_literal().decode())

main()
