# Iterate keys via d.keys(), check len() and 'in' operator
def main() -> None:
    d = {"a": 1, "b": 2, "c": 3}

    for k in d.keys():
        print(k)

    print(len(d.keys()))
    print("b" in d.keys())
    print("z" in d.keys())

main()
