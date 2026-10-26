from tpy import int32


class Err(Exception):
    def __init__(self, code: int32) -> None:
        super().__init__("err")
        self.code = code
