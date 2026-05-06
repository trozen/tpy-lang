# D16 phase 9: delattr(obj, name_var) with a runtime name -- routes to
# __delattr__ unconditionally.


class Tracker:
    _last_deleted: str

    def __init__(self) -> None:
        self._last_deleted = ""

    def __delattr__(self, name: str) -> None:
        self._last_deleted = name


def del_it(t: Tracker, name: str) -> None:
    delattr(t, name)


def main() -> None:
    t = Tracker()
    for k in ["host", "port"]:
        del_it(t, k)
    print(t._last_deleted)


main()
