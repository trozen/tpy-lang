# Tests __del__ destructor: cleanup is called deterministically at end of scope (C++ RAII),
# both for local variables and globals

class Resource:
    name: str
    def __init__(self, name: str):
        self.name = name
    def __del__(self):
        print("destroying", self.name)

g = Resource("global")

def main():
    r = Resource("local")
    print("alive")

main()
