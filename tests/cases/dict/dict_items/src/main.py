# Iterate items via d.items() with tuple unpacking, check len()
def main() -> None:
    d = {"one": 1, "two": 2, "three": 3}

    for k, v in d.items():
        print(k, v)

    print(len(d.items()))

main()
