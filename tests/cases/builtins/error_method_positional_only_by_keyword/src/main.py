# dict.get takes key and default positionally only, as in CPython
# (TypeError: dict.get() takes no keyword arguments): each variant says why
# it refuses the call (pinned in diag.txt).


def main() -> None:
    d = {"a": 1}
    print(d.get("a", default=0))  # tpyc: error(/No matching overload for 'get' with argument types \(str, default=int\)/)


main()
