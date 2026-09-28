# raise <expr>: raise pre-constructed exception variables and function results
from tpy import int32, Own

class AppError(Exception):
    code: int32
    def __init__(self, code: int32) -> None:
        super().__init__()
        self.code = code

class OtherError(Exception):
    tag: str
    def __init__(self, tag: str) -> None:
        super().__init__()
        self.tag = tag

def make_error(code: int32) -> Own[AppError]:
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
