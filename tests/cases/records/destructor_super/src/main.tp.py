# Tests super().__del__() as last statement: call is dropped (parent dtor is automatic),
# no warning emitted

class Base:
    def __del__(self):
        print("Base destroyed")

class Child(Base):
    label: str
    def __init__(self, label: str):
        self.label = label
    def __del__(self):
        print("Child destroyed:", self.label)
        super().__del__()

def main():
    c = Child("hello")
    print("alive")

main()
