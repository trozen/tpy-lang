# else block runs only when no exception is raised
def fail(do_raise: bool) -> None:
    if do_raise:
        raise ValueError("fail")

def main() -> None:
    # Success: else runs
    try:
        fail(False)
    except ValueError:
        print("caught")
    else:
        print("else 1")

    # Error: else does NOT run
    try:
        fail(True)
    except ValueError:
        print("caught")
    else:
        print("else 2")

main()
