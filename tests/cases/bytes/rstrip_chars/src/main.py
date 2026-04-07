# bytes.rstrip(chars) -- strip specific bytes from right
def main() -> None:
    print(b"hello\x00\x00\x00".rstrip(b"\x00"))  # b'hello'
    print(b"hello...".rstrip(b"."))                # b'hello'
    print(b"abcba".rstrip(b"ab"))                  # b'abc' -- strips any 'a' or 'b' from right
    print(b"hello".rstrip(b"\x00"))                # b'hello' -- nothing to strip
    # BytesView (from slice) -- rstrip returns a view
    data: bytes = b"hello\x00\x00\x00end"
    print(len(data[0:8].rstrip(b"\x00")))          # 5
main()
