# Union-annotated container with incompatible element type is rejected
def main() -> None:
    d: dict[str, int | str] = {"a": [1, 2]}  # tpyc: error(/incompatible/)
