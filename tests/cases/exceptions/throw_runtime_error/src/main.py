# RuntimeError -- generic catchall when no more specific Exception subtype
# fits. Common Python idiom: `raise RuntimeError(...)` for "this shouldn't
# happen" runtime conditions.


def fail() -> None:
    raise RuntimeError("custom: state invariant broken")


def main() -> None:
    try:
        fail()
    except RuntimeError as e:
        print("caught RuntimeError:", str(e))


main()
