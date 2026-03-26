# Bytes slicing: start:stop with positive, negative, and omitted bounds
def main() -> None:
    data = b"hello world"
    print(data[0:5])
    print(data[6:11])
    print(data[-5:])
    print(data[:5])
    print(data[3:3])

    # Slice of a literal
    print(b"abcdef"[1:4])

    # Slice passed to function
    chunk = data[0:5]
    print(len(chunk))
    print(chunk)

main()
