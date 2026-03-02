# Overwriting a key preserves insertion order
def main() -> None:
    d = {"a": 1, "b": 2, "c": 3}
    d["a"] = 10
    d["b"] = 20
    print(d)
    for k in d:
        print(k)

main()
