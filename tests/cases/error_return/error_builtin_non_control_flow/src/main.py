# @error_return(Exception) is rejected -- Exception is not a ControlFlow type
from tpy import Int32, error_return

@error_return(Exception)
def parse(s: str) -> Int32:  # tpyc: error(/not a ControlFlow type/)
    if s == "1":
        return 1
    raise Exception
