# zip() called with a single argument (unsupported arity)

def main() -> None:
    zip([1, 2])  # tpyc: error(/No matching overload/)

main()
