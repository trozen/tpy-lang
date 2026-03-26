# str.encode() and bytes.decode() round-trip
def main() -> None:
    s = "hello bytes"
    encoded = s.encode()
    print(encoded)

    decoded = encoded.decode()
    print(decoded)
    print(s == decoded)

    combined = b"prefix:" + s.encode()
    print(combined)

main()
