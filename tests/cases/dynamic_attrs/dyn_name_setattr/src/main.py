# D16 phase 9: setattr(obj, name_var, v) with a runtime name -- routes to
# __setattr__ unconditionally.


class Headers:
    _last_name: str
    _last_value: str

    def __init__(self) -> None:
        self._last_name = ""
        self._last_value = ""

    def __setattr__(self, name: str, value: str) -> None:
        self._last_name = name
        self._last_value = value


def set_it(h: Headers, name: str, value: str) -> None:
    setattr(h, name, value)


def main() -> None:
    h = Headers()
    keys = ["host", "port"]
    for k in keys:
        set_it(h, k, "v-" + k)
    print(h._last_name, h._last_value)


main()
