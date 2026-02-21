# Tests error: super().__del__() must be the last statement in __del__

class Base:
    def __del__(self):
        pass

class Child(Base):
    def __del__(self):
        super().__del__()  # tpyc: error(/super\(\).__del__\(\) must be the last statement/)
        pass
