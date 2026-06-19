# A str-view key (param/local) works in `k in dict[str,str]` / `k in set[str]`
# membership, not just a literal -- the heterogeneous contains overload.
def dict_has(k: str, d: dict[str, str]) -> bool:
    return k in d


def dict_missing(k: str, d: dict[str, str]) -> bool:
    return k not in d


def set_has(k: str, s: set[str]) -> bool:
    return k in s


def set_missing(k: str, s: set[str]) -> bool:
    return k not in s


def main():
    d: dict[str, str] = {}
    d["alpha"] = "1"
    d["beta"] = "2"
    # view-typed key (the param) against a str-keyed dict
    print(dict_has("alpha", d), dict_has("gamma", d))
    print(dict_missing("gamma", d), dict_missing("alpha", d))
    # a view key built from a slice, not a literal
    name = ("xalpha")[1:]
    print(name in d)
    # literal key still works (inverse: non-template overload)
    print("beta" in d, "zeta" in d)

    s: set[str] = set()
    s.add("x")
    print(set_has("x", s), set_has("y", s))
    print(set_missing("y", s), set_missing("x", s))
    print("x" in s)


main()
