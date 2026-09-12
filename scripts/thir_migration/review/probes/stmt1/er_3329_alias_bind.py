from tpy import int32, Own, error_return, ReturnException
class Err(Exception, ReturnException):
    pass
class Cat:
    n: int32
    def __init__(self) -> None:
        self.n = 1
class Dog:
    n: int32
    def __init__(self) -> None:
        self.n = 2
class H:
    c: Cat
    def __init__(self) -> None:
        self.c = Cat()
    @error_return(Err)
    def vc(self) -> Cat:
        return self.c
def main() -> None:
    h = H()
    d = Dog()
    p: Cat | Dog = d
    try:
        p = h.vc()
    except Err:
        print("error")
    if isinstance(p, Cat):
        print(p.n)
main()
