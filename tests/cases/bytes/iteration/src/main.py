# Bytes iteration, indexing, contains, concat, repeat
def main() -> None:
    data = b"ABC"
    for b in data:
        print(b)

    print(data[0])
    print(data[2])
    print(data[-1])

    print(65 in data)
    print(0 in data)

    a = b"hello"
    b2 = b" world"
    print(a + b2)
    print(a * 3)

main()
