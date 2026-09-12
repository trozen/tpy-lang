# **kwargs: passing wrong TypedDict type via **expr
from typing import TypedDict, Unpack
from tpy import int32

class Options(TypedDict):
    host: str
    port: int32

class WrongType(TypedDict):
    name: str
    value: int32

def connect(**kwargs: Unpack[Options]) -> None:
    pass

def main() -> None:
    w = WrongType(name="test", value=int32(1))
    connect(**w)  # tpyc: error(/expected Options, got WrongType/)

main()
