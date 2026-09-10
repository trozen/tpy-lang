# Test open() binary mode: write/read round-trip, readline/readlines, context manager
def main() -> None:
    path = "tpy_test_open_binary.bin"

    # Write binary data
    w = open(path, "wb")
    w.write(b"\x00\x01\x02\x03")
    w.close()

    # Read back
    r = open(path, "rb")
    data = r.read()
    r.close()
    print(len(data))
    print(data[0], data[1], data[2], data[3])

    # Context manager write
    with open(path, "wb") as f1:
        f1.write(b"\xaa\xbb")

    # Context manager read
    with open(path, "rb") as f2:
        data2 = f2.read()
    print(len(data2))

    # Append mode
    with open(path, "ab") as f3:
        f3.write(b"\xcc")

    with open(path, "rb") as f4:
        data3 = f4.read()
    print(len(data3))

    # readline / readlines with newline-delimited binary data
    with open(path, "wb") as f5:
        f5.write(b"alpha\nbeta\ngamma")

    r2 = open(path, "rb")
    first = r2.readline()
    second = r2.readline()
    third = r2.readline()
    r2.close()
    print(len(first), len(second), len(third))

    r3 = open(path, "rb")
    lines = r3.readlines()
    r3.close()
    print(len(lines))

main()
