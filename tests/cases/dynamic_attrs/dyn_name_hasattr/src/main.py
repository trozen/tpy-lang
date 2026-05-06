# D16 phase 9: hasattr(obj, name_var) with a runtime name -- requires
# the receiver to be dyn-readable; routes to __getattr__ and reports
# True/False based on whether AttributeError is raised.


class Headers:
    def __getattr__(self, name: str) -> str:
        if name == "host":
            return "example.com"
        raise AttributeError(name)


def has(h: Headers, name: str) -> bool:
    return hasattr(h, name)


def main() -> None:
    h = Headers()
    for k in ["host", "missing"]:
        print(k, has(h, k))


main()
