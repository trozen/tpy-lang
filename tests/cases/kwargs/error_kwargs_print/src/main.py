# Error: print() rejects unsupported keyword arguments
def main() -> None:
    print("hello", sep=",")  # tpyc: error(/does not support keyword argument 'sep'/)

main()
