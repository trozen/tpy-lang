# Dict methods: get, pop, pop with default, clear
def main() -> None:
    d = {"a": 10, "b": 20, "c": 30}

    # get returns Optional
    v = d.get("a")
    print(v)
    v2 = d.get("missing")
    print(v2)

    # pop removes and returns
    p = d.pop("c")
    print(p)
    print(len(d))

    # pop with default
    p2 = d.pop("missing", 99)
    print(p2)

    # clear
    d.clear()
    print(len(d))
    print(d)

main()
