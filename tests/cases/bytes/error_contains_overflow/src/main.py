# Int literal too large for UInt8 in bytes membership test
def main() -> None:
    data = b"hello"
    print(256 in data)  # tpyc: error(/expected UInt8/)

main()
