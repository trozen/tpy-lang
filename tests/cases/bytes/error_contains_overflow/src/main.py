# Int literal too large for uint8 in bytes membership test
def main() -> None:
    data = b"hello"
    print(256 in data)  # tpyc: error(/expected uint8/)

main()
