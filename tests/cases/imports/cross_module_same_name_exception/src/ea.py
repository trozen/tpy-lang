class Err(Exception):
    def __init__(self, code: int):
        super().__init__()
        self.code = code
