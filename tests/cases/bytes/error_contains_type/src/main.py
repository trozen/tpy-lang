# Type mismatch in bytes membership test: str is not UInt8
def main() -> None:
    data = b"hello"
    print("h" in data)  # tpyc: error(/expected UInt8/)

main()
