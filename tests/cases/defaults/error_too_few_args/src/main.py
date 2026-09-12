# Error: too few arguments (fewer than minimum required)
from tpy import int32

def connect(host: str, port: int32, timeout: int32 = int32(30)) -> None:
    pass

connect()  # tpyc: error(/expects 2 to 3 arguments, got 0/)
