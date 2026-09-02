from tpy import Int32
class Dog:
    def sound(self) -> str:
        return "woof"
class Cat:
    def sound(self) -> str:
        return "meow"
async def step(n: Int32) -> Int32:
    return n + 1
async def f(a: Dog | Cat, n: Int32) -> Int32:
    try:
        n = await step(n)
    finally:
        if isinstance(a, Dog):
            return 0
        print(a.sound())
    return n
def main() -> None:
    pass
main()
