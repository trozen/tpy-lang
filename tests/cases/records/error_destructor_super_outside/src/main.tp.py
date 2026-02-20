# Tests error: super().__del__() called outside __del__

class Base:
    def __del__(self):
        pass

class Child(Base):
    def cleanup(self):
        super().__del__()  # tpyc: error(/super\(\).__del__\(\) can only be called inside __del__/)
