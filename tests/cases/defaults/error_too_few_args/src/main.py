# Error: too few arguments (fewer than minimum required)
from tpy import Int32

def connect(host: str, port: Int32, timeout: Int32 = Int32(30)) -> None:
    pass

connect()  # tpyc: error(/expects 2 to 3 arguments, got 0/)
