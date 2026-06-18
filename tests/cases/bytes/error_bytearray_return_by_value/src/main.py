# bytearray is a reference type: a function returning a fresh bytearray by bare
# annotation returns by reference and would dangle, exactly like list/dict/set.
# It must get the clean "use Own[bytearray]" diagnostic, not a raw C++ error.
def make() -> bytearray:
    return bytearray(b"hi")  # tpyc: error(/Use Own\[bytearray\] to return by value/)

def main() -> None:
    print(len(make()))

main()
