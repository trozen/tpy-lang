# Tests error: super().__del__() with arguments

class Base:
    def __del__(self):
        pass

class Child(Base):
    def __del__(self):
        super().__del__(42)  # tpyc: error(/super\(\).__del__\(\) takes no arguments/)
