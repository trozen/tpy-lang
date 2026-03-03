# Iterate values via d.values(), check len() and 'in' operator
def main() -> None:
    d = {"x": 10, "y": 20, "z": 30}

    for v in d.values():
        print(v)

    print(len(d.values()))
    print(20 in d.values())
    print(99 in d.values())

main()
