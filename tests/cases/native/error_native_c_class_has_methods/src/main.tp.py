from tpy import native_c, Int32

@native_c
class Point:  # tpyc: error(/Methods on @native_c classes are not yet supported/)
    x: Int32
    y: Int32
    def distance(self) -> Int32:
        return self.x

def main() -> None:
    pass
