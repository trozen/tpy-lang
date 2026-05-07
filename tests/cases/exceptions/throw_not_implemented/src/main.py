# NotImplementedError is the standard way to mark an abstract / unimplemented
# method body. Catchable like any other Exception subclass.


def stub() -> None:
    raise NotImplementedError("stub: not implemented yet")


def main() -> None:
    try:
        stub()
    except NotImplementedError as e:
        print("caught:", str(e))


main()
