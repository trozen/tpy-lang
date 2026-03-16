# ord() rejects non-character/non-string types.
def main() -> None:
    print(ord(42))  # tpyc: error(/No matching overload for ord/)

main()
