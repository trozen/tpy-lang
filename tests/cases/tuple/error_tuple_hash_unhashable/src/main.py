# Error: hash() on tuple with unhashable element type

def main() -> None:
    t = ([1, 2],)
    hash(t)  # tpyc: error(/No matching overload/)

main()
