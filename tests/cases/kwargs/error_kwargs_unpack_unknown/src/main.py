# **kwargs: unknown keyword argument at call site
from typing import TypedDict, Unpack
from tpy import int32

class Options(TypedDict):
    host: str
    port: int32

def connect(**kwargs: Unpack[Options]) -> None:
    pass

def main() -> None:
    connect(host="localhost", timeout=int32(30))  # tpyc: error(/unexpected keyword argument 'timeout'/)

main()
