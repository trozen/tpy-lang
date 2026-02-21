# @readonly(False) on __init__ allows constructing objects inside @readonly functions.
from tpy import readonly

class Logger:
    @readonly(False)
    def __init__(self) -> None:
        pass


@readonly
def ok() -> int:
    Logger()  # tpyc: ok
    return 0


print(ok())
