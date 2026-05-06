# raise AttributeError(...) is a throw-tier exception. Catchable via
# try/except. CPython parity: AttributeError is a CPython builtin.


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
