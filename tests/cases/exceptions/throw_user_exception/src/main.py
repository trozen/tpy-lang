# User-defined throw exception with data fields
from tpy import int32

class AppError(Exception):
    code: int32
    detail: str

    def __init__(self, code: int32, detail: str) -> None:
        super().__init__()
        self.code = code
        self.detail = detail

def validate(x: int32) -> None:
    if x < 0:
        raise AppError(1, "negative value")
    if x > 100:
        raise AppError(2, "too large")

def main() -> None:
    try:
        validate(-5)
    except AppError as e:
        print(e.code)
        print(e.detail)

    try:
        validate(200)
    except AppError as e:
        print(e.code)
        print(e.detail)

    try:
        validate(50)
        print("ok")
    except AppError as e:
        print(e.detail)

main()
