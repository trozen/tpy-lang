import asyncio
from typing import Protocol
from tpy import Int32
class Speaker(Protocol):
    def speak(self) -> Int32: ...
class Dog:
    def speak(self) -> Int32:
        return 1
async def use(x: Speaker) -> Int32:
    await asyncio.sleep(0)
    if isinstance(x, Speaker):
        return x.speak()
    return 0
def main() -> None:
    pass
main()
