# Tuple global with bare int literals (tests resolve_int_literals for globals)
t = (1, 2, 3, 4)
t2 = (10, (20, 30))

def main() -> None:
    print(t)
    print(t2)

main()
