# isinstance on an unbounded type parameter is rejected: a generic body is
# compiled once for all instantiations, so the check cannot be resolved.
class Animal:
    def __init__(self):
        pass

def f[T](x: T) -> bool:
    return isinstance(x, Animal)  # tpyc: error(/unbounded type parameter 'T'/)

def main():
    print(f(Animal()))

main()
