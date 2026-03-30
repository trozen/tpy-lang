# nested try/finally: both finally blocks run in correct order
from tpy import Int32

def nested_return() -> Int32:
    """Return propagates through nested finally blocks."""
    try:
        try:
            return 10
        finally:
            print("inner")
    finally:
        print("outer")

def nested_raise() -> None:
    """Exception propagates through nested finally blocks."""
    try:
        try:
            try:
                raise ValueError("deep")
            finally:
                print("innermost")
        finally:
            print("middle")
    except ValueError:
        print("caught")

def main() -> None:
    print(nested_return())
    nested_raise()

main()
