# raise inside finally replaces the pending exception
def test_raise_in_finally() -> None:
    """Raise in finally replaces the original exception."""
    try:
        try:
            raise ValueError("original")
        finally:
            raise ValueError("replacement")
    except ValueError:
        print("caught replacement")

def test_raise_in_finally_no_exception() -> None:
    """Raise in finally when try body succeeds."""
    try:
        try:
            print("try body ok")
        finally:
            raise ValueError("from finally")
    except ValueError:
        print("caught from finally")

def main() -> None:
    test_raise_in_finally()
    test_raise_in_finally_no_exception()

main()
