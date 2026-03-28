# zip() called with no arguments (unsupported arity)

def main() -> None:
    zip()  # tpyc: error(/No matching overload/)

main()
