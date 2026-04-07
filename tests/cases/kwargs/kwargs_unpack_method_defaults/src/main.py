# **kwargs: method with all-default TypedDict fields, zero kwargs call
from typing import TypedDict, Unpack
from tpy import Int32

class Config(TypedDict):
    host: str = "localhost"
    port: Int32 = Int32(8080)

class Server:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def start(self, **kwargs: Unpack[Config]) -> None:
        print(self.name)
        print(kwargs["host"])
        print(kwargs["port"])

def main() -> None:
    s = Server(name="MyServer")
    s.start()
    s.start(port=Int32(9090))

main()
