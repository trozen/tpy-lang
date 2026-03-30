# raise <expr>: raise pre-constructed exception variables and function results
from tpy import Int32, Own

class AppError(Exception):
    code: Int32
    def __init__(self, code: Int32) -> None:
        self.code = code

class OtherError(Exception):
    tag: str
    def __init__(self, tag: str) -> None:
        self.tag = tag

def make_error(code: Int32) -> Own[AppError]:
    return AppError(code)

def test_raise_variable() -> None:
    """Raise a pre-constructed exception variable."""
    try:
        e = AppError(10)
        raise e
    except AppError as caught:
        print(caught.code)

def test_raise_reassigned() -> None:
    """Raise after conditional reassignment."""
    try:
        e = AppError(1)
        e = AppError(2)
        raise e
    except AppError as caught:
        print(caught.code)

def test_raise_different_types() -> None:
    """Raise different exception types from variables."""
    try:
        e = OtherError("first")
        raise e
    except OtherError as caught:
        print(caught.tag)

def test_raise_function_result() -> None:
    """Raise the result of a function call."""
    try:
        raise make_error(7)
    except AppError as caught:
        print(caught.code)

def main() -> None:
    test_raise_variable()
    test_raise_reassigned()
    test_raise_different_types()
    test_raise_function_result()

main()
