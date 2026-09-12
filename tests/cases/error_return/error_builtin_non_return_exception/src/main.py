# @error_return(Exception) is rejected -- Exception is not a ReturnException type
from tpy import int32, error_return

@error_return(Exception)
def parse(s: str) -> int32:  # tpyc: error(/not a ReturnException type/)
    if s == "1":
        return 1
    raise Exception
