# Views on empty dict: iteration produces nothing, len is 0
def main() -> None:
    d: dict[str, int] = {}

    for k in d.keys():
        print(k)

    for v in d.values():
        print(v)

    for k, v in d.items():
        print(k, v)

    print(len(d.keys()))
    print(len(d.values()))
    print(len(d.items()))

main()
