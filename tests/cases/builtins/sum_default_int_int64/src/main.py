# sum over an element-less list takes the default int (int64).
def main() -> None:
    # The empty list's element is the default int.
    e = sum([])  # tpyc: type(int64)
    print(e)
    # So is the start literal's.
    s = sum([], 5)  # tpyc: type(int64)
    print(s)

main()
