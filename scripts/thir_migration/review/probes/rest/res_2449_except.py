from tpy import int32
class Dog:
    def sound(self) -> str:
        return "woof"
class Cat:
    def sound(self) -> str:
        return "meow"
async def step(n: int32) -> int32:
    return n + 1
async def f(a: Dog | Cat) -> int32:
    t = 0
    if isinstance(a, Dog):
        try:
            t = await step(1)
        except ValueError:
            print(a.sound())
    return t
def main() -> None:
    pass
main()
