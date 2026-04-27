# Error: print() rejects a file= argument that does not satisfy Writable.
def main() -> None:
    print("hello", file=42)  # tpyc: error(/'file' argument must satisfy the Writable protocol/)

main()
