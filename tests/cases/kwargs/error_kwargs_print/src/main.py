# Error: print() rejects unsupported keyword arguments
def main() -> None:
    print("hello", bogus=42)  # tpyc: error(/does not support keyword argument 'bogus'/)

main()
