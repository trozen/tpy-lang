# D16 phase 9: getattr(obj, name_var) with a runtime name -- routes to
# __getattr__ unconditionally (Option A: route-all-to-dunder).


class Headers:
    def __getattr__(self, name: str) -> str:
        if name == "host":
            return "example.com"
        if name == "port":
            return "8080"
        raise AttributeError(name)


def lookup(h: Headers, name: str) -> str:
    return getattr(h, name)


def main() -> None:
    h = Headers()
    print(lookup(h, "host"))
    print(lookup(h, "port"))
    try:
        print(lookup(h, "missing"))
    except AttributeError as e:
        print("caught:", str(e))


main()
