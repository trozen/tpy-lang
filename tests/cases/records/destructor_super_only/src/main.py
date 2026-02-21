# Tests __del__ whose body is only super().__del__() -- effective body is empty

class Base:
    def __del__(self):
        print("Base destroyed")

class Child(Base):
    def __del__(self):
        super().__del__()

def main():
    c = Child()
    print("alive")

main()
