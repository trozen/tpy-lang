# An empty dict or set a non-number seeds takes its parts' types from the
# first store, so a method called on it afterwards binds them.
def main() -> None:
    u = {}  # tpyc: type(dict[str, str])
    u["a"] = "x"
    w = u.setdefault("b", "y")  # tpyc: ok
    print("setdefault:", w, sorted(u.keys()))  # tpyc: ok
    u.update({"c": "z"})  # tpyc: ok
    print("pop:", u.pop("a"), len(u))  # tpyc: ok
    for k, v in u.items():  # tpyc: ok
        print("items:", k, v)
    # the set's first add seeds its element; the lookup then binds it
    s = set()  # tpyc: type(set[str])
    s.add("p")
    print("set:", sorted(s), "p" in s)  # tpyc: ok


main()
