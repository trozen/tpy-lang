# **kwargs: unknown keyword argument at call site
from typing import TypedDict, Unpack
from tpy import Int32

class Options(TypedDict):
    host: str
    port: Int32

def connect(**kwargs: Unpack[Options]) -> None:
    pass

def main() -> None:
    connect(host="localhost", timeout=Int32(30))  # tpyc: error(/unexpected keyword argument 'timeout'/)

main()
