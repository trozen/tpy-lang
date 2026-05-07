# Explicit `raise OSError(...)` is the throw-tier path; user code can
# catch it the same way as runtime-thrown OSError. Subclasses
# (FileNotFoundError) inherit the throw behavior.


def fail() -> None:
    raise OSError("custom: simulated I/O failure")


def main() -> None:
    try:
        fail()
    except OSError as e:
        print("caught:", str(e))


main()
