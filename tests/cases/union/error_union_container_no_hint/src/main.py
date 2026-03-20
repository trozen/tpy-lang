# Union container literals without annotation are rejected
def main() -> None:
    d = {"a": 1, "b": "hello"}  # tpyc: error(/mixed value types/)
