# TypedDict: methods are not allowed
from typing import TypedDict

class Info(TypedDict):
    name: str
    def greet(self) -> str:  # tpyc: error(/Methods are not allowed on TypedDict/)
        return "hi"
