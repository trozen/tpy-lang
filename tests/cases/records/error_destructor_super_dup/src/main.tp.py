# Tests error: duplicate super().__del__() calls

class Base:
    def __del__(self):
        pass

class Child(Base):
    def __del__(self):
        super().__del__()
        super().__del__()  # tpyc: error(/super\(\).__del__\(\) can only be called once/)
