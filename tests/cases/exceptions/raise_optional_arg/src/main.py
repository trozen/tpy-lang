# raise X(args) coerces ctor args like a normal call: None into a value-Optional
# param lowers to std::nullopt (was nullptr); inverse non-None arg still works.
from tpy import int32


class MyErr(Exception):
    e: int32 | None

    def __init__(self, e: int32 | None) -> None:
        super().__init__("boom")
        self.e = e


def main() -> None:
    try:
        raise MyErr(None)
    except MyErr as ex:
        print(ex.e is None)
    try:
        raise MyErr(7)
    except MyErr as ex:
        if ex.e is not None:
            print(ex.e)


main()
