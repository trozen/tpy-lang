# Auto-declare is disabled for classes with base classes (Phase 1)
from tpy import Int32


class Base:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x


class Child(Base):
    def __init__(self, x: Int32, y: Int32):
        super().__init__(x)
        self.y = y  # tpyc: error(/no field/)
