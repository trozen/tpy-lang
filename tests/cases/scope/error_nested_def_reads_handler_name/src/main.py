# A nested def reading the name only an `except ... as e` handler binds,
# after the handler: the handler's end deletes it, so the closure's read is
# CPython's UnboundLocalError on every path.
def f(flag: bool) -> None:
    try:
        if flag:
            raise ValueError("x")
    except ValueError as e:
        print("h")

    def inner() -> str:
        # The capture of `e`, unbound after the handler.
        return str(e)  # tpyc: error(/variable 'e' may not be assigned at this point/)
    print(inner())


f(False)
