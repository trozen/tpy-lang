# A generic subclass infers a field whose type is the class type parameter.
class Holder:
    pass

class Box[T](Holder):
    def __init__(self, v: T):
        self.v = v

def main() -> None:
    print(Box(5).v)
    print(Box("hi").v)

main()
