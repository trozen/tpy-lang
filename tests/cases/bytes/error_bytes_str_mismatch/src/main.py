# bytes and str are not interchangeable
def main() -> None:
    s: str = b"hello"  # tpyc: error(/expected str, got bytes/)

main()
