# finally block runs on both success and error paths
from tpy import Int32

def fail(do_raise: bool) -> None:
    if do_raise:
        raise ValueError("fail")

def main() -> None:
    # Success path: finally runs after try body
    try:
        fail(False)
    except ValueError:
        print("caught")
    finally:
        print("finally 1")

    # Error path: finally runs after except body
    try:
        fail(True)
    except ValueError:
        print("caught")
    finally:
        print("finally 2")

    # try/finally only (no except)
    try:
        print("try body")
    finally:
        print("finally 3")

main()
