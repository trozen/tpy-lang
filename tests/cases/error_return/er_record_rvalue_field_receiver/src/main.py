# A field read off an `@error_return` call's record result: the unwrap binds
# a temporary and the field access composes on top of it.
from tpy import int32, Own, ReturnException, error_return


class E(Exception, ReturnException):
    pass


class Data:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v


@error_return(E)
def make_data(v: int32) -> Own[Data]:
    if v < 0:
        raise E()
    return Data(v)


@error_return(E)
def get_value(v: int32) -> int32:
    # The receiver of `.value` is the unwrapped call result.
    return make_data(v).value


def main() -> None:
    try:
        print(get_value(4))
        print(get_value(-1))
    except E:
        print("raised")


main()
