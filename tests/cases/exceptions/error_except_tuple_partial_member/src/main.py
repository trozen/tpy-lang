# Error: an `except (A, B) as e:` body must type-check under every element's
# type, since each element becomes its own clause binding `e` at that exact
# type. Here `.code` exists on AErr but not BErr, and the diagnostic names the
# element that doesn't support the body.
class AErr(Exception):
    def __init__(self, code: int) -> None:
        super().__init__()
        self.code = code


class BErr(Exception):
    pass


def main() -> None:
    try:
        raise AErr(1)
    except (AErr, BErr) as e:
        print(e.code)  # tpyc: error(/Record 'BErr' has no field 'code'/)


main()
