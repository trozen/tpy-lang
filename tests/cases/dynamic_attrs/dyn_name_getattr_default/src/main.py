# D16 phase 9: 3-arg getattr(obj, name_var, default) with a runtime name --
# routes to __getattr__; AttributeError caught and `default` substituted.


class Headers:
    def __getattr__(self, name: str) -> str:
        if name == "host":
            return "example.com"
        raise AttributeError(name)


def lookup(h: Headers, name: str) -> str:
    return getattr(h, name, "fallback")


def main() -> None:
    h = Headers()
    keys = ["host", "missing", "other"]
    for k in keys:
        print(k, "=>", lookup(h, k))


main()
