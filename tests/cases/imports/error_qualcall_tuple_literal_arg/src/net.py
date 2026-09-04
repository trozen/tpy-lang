def request(url: str, auth: tuple[str, str] | None = None) -> int:
    return len(url) if auth is None else len(url) + len(auth[0])
