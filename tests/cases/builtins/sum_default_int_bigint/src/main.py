# sum over an element-less list takes the default int (BigInt).
def main() -> None:
    # The empty list's element is the default int.
    e = sum([])  # tpyc: type(int)
    print(e)
    # So is the start literal's.
    s = sum([], 5)  # tpyc: type(int)
    print(s)

main()
