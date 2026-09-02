from tpy import Int32
class Dog:
    def sound(self) -> str:
        return "woof"
class Cat:
    def sound(self) -> str:
        return "meow"
async def step(n: Int32) -> Int32:
    return n + 1
class G:
    def __enter__(self) -> Int32:
        return 1
    def __exit__(self, et, ev, tb) -> None:
        pass
class H:
    g: G
    def __init__(self) -> None:
        self.g = G()
async def f(h: H) -> Int32:
    with h.g as v:
        await step(v)
    return 1
def main() -> None:
    pass
main()
