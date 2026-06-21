class Err(Exception):
    def __init__(self, msg: str):
        self.msg = msg
