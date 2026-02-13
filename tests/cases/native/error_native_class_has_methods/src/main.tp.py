from tpy import native, Int32

@native
class Vec2:  # tpyc: error(/Methods on @native classes are not yet supported/)
    x: Int32
    y: Int32
    def length(self) -> Int32:
        return self.x

def main() -> None:
    pass
