# **kwargs: Unpack[TypedDict] on a method
from typing import TypedDict, Unpack
from tpy import Int32

class Options(TypedDict):
    host: str
    port: Int32

class Client:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def connect(self, **kwargs: Unpack[Options]) -> None:
        print(self.name)
        print(kwargs["host"])
        print(kwargs["port"])

def main() -> None:
    c = Client(name="MyClient")
    c.connect(host="localhost", port=Int32(8080))

main()
