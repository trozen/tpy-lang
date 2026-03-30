# Error: raise <expr> with ControlFlow exception type
from tpy import ControlFlow, Int32

class NotFound(Exception, ControlFlow):
    code: Int32
    def __init__(self, code: Int32) -> None:
        self.code = code

def test(e: NotFound) -> None:
    raise e  # tpyc: error(/raise <expr>.*cannot be used with ControlFlow/)
