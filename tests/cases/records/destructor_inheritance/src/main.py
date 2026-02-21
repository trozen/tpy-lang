# Tests __del__ with inheritance: parent destructor is called automatically after child
# In C++ destructor order is child-first, then parent (opposite of __init__)

class Base:
    def __del__(self):
        print("Base destroyed")

class Child(Base):
    label: str
    def __init__(self, label: str):
        self.label = label
    def __del__(self):
        print("Child destroyed:", self.label)

def main():
    c = Child("hello")
    print("alive")

main()
