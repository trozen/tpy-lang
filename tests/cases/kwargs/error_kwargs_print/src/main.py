# Error: print() rejects unsupported keyword arguments
def main() -> None:
    print("hello", file="out.txt")  # tpyc: error(/does not support keyword argument 'file'/)

main()
