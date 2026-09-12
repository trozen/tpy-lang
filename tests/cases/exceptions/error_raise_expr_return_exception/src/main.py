# Error: raise <expr> with ReturnException exception type
from tpy import ReturnException, int32

class NotFound(Exception, ReturnException):
    code: int32
    def __init__(self, code: int32) -> None:
        self.code = code

def test(e: NotFound) -> None:
    raise e  # tpyc: error(/raise <expr>.*cannot be used with ReturnException/)
