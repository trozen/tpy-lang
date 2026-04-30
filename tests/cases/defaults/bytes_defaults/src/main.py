# Bytes literal defaults: b"" and b"..." for bytes and BytesView params.
from tpy import BytesView

def with_bytes(data: bytes = b"") -> int:
    return len(data)

def with_bytes_default(data: bytes = b"hi") -> int:
    return len(data)

def with_view_empty(data: BytesView = b"") -> int:
    return len(data)

def with_view_default(data: BytesView = b"hi") -> int:
    return len(data)

def main() -> None:
    print(with_bytes())
    print(with_bytes(b"abc"))
    print(with_bytes_default())
    print(with_bytes_default(b"abcdef"))
    print(with_view_empty())
    print(with_view_default())

main()
