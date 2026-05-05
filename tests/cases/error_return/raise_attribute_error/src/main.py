# AttributeError is a ReturnException -- raised via @error_return(AttributeError),
# caught by try/except. CPython parity: AttributeError is a CPython builtin.
from tpy import error_return


@error_return(AttributeError)
def looker(name: str) -> str:
    if name == "host":
        return "example.com"
    raise AttributeError(name)


def main() -> None:
    try:
        v = looker("host")
        print(v)
        v = looker("missing")
        print("never")
    except AttributeError as e:
        print("caught:", str(e))


main()
