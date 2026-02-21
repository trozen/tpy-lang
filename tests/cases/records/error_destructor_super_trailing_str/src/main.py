# Tests error: trailing string literal after super().__del__() is not a docstring

class Base:
    def __del__(self):
        pass

class Child(Base):
    def __del__(self):
        super().__del__()  # tpyc: error(/super\(\).__del__\(\) must be the last statement/)
        "not a docstring"
