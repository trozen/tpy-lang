# Bytes as function parameters (passed as span), boolean context
def byte_len(data: bytes) -> int:
    return len(data)

def first_byte(data: bytes) -> int:
    return data[0]

def is_empty(data: bytes) -> bool:
    if data:
        return False
    return True

def copy_bytes(data: bytes) -> bytes:
    return bytes(data)

def main() -> None:
    b = b"hello"
    print(byte_len(b))
    print(first_byte(b))
    print(is_empty(b))
    print(is_empty(b""))

    copied = copy_bytes(b)
    print(copied)
    print(copied == b)

main()
