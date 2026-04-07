# bytes.upper() -- ASCII uppercase
def main() -> None:
    print(b"hello".upper())       # b'HELLO'
    print(b"Hello World".upper()) # b'HELLO WORLD'
    print(b"ALREADY".upper())     # b'ALREADY'
    print(b"123abc".upper())      # b'123ABC'
    # bytearray too
    ba = bytearray(b"mixed Case")
    print(ba.upper())             # bytearray(b'MIXED CASE')
    # BytesView (from slice)
    print(b"hello world"[0:5].upper())   # b'HELLO'
    # chain: slice -> rstrip -> upper
    print(b"hello\x00\x00"[0:7].rstrip(b"\x00").upper())  # b'HELLO'
main()
