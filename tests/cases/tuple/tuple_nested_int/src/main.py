# Nested tuples with bare int literals (no explicit Int32)
def main() -> None:
    t = ((1, 2), (3, 4))
    print(t)
    print(t[0])
    print(t[1][1])

main()
