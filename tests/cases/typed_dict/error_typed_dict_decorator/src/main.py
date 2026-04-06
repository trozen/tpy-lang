# TypedDict: decorators are not supported
from typing import TypedDict
from tpy import nocopy

@nocopy
class Info(TypedDict):  # tpyc: error(/Decorators are not supported on TypedDict/)
    name: str
