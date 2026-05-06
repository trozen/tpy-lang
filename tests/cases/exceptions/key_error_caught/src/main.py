# Dict miss / set.remove(missing) / set.pop() empty all throw KeyError --
# catchable in user code.


def main() -> None:
    d: dict[str, int] = {"a": 1}
    try:
        print(d["missing"])
    except KeyError as e:
        print("caught:", str(e))

    try:
        d.pop("missing")
    except KeyError as e:
        print("caught:", str(e))

    try:
        del d["missing"]
    except KeyError as e:
        print("caught:", str(e))

    s: set[int] = {1, 2, 3}
    try:
        s.remove(99)
    except KeyError as e:
        print("caught:", str(e))

    empty: set[int] = set()
    try:
        empty.pop()
    except KeyError as e:
        print("caught:", str(e))


main()
