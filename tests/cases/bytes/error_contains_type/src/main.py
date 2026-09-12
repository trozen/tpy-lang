# Type mismatch in bytes membership test: str is not uint8
def main() -> None:
    data = b"hello"
    print("h" in data)  # tpyc: error(/expected uint8/)

main()
