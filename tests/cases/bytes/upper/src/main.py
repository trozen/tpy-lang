# bytes.upper() -- ASCII uppercase
def main() -> None:
    print(b"hello".upper())       # b'HELLO'
    print(b"Hello World".upper()) # b'HELLO WORLD'
    print(b"ALREADY".upper())     # b'ALREADY'
    print(b"123abc".upper())      # b'123ABC'
    # bytearray too
    ba = bytearray(b"mixed Case")
    print(ba.upper())             # bytearray(b'MIXED CASE')
main()
