# log() macro should error when no _logger field or get_logger() is found.
from log_macro import log


class NoLogger:
    value: str

    def __init__(self, value: str) -> None:
        self.value = value

    def do_work(self) -> None:
        log(f"v={self.value}")  # tpyc: error(/no _logger.*get_logger/)

def main() -> None:
    pass

main()
