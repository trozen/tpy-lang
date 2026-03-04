# Dict with BigInt (int) keys -- verifies std::hash<BigInt> works at C++ level
def main() -> None:
    d: dict[int, str] = {1: "one", 2: "two", 3: "three"}
    print(d[1])
    print(d[2])
    print(1 in d)
    print(99 in d)
    print(len(d))

main()
