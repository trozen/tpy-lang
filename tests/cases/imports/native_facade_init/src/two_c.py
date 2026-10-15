print("two_consumers: two_c init")
hits: list[int] = []


def hit(n: int) -> int:
    hits.append(n)
    return len(hits)
