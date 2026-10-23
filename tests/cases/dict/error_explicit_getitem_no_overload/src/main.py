# The explicit dunder call keeps the call diagnostic: the user wrote the
# dunder, so a wrong key is "No matching overload", not the subscript wording.


def main() -> None:
    d = {"a": 1}
    print(d.__getitem__(5))  # tpyc: error(/No matching overload for '__getitem__' with argument types \(int\)/)


main()
