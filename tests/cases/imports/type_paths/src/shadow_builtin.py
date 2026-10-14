# mx's own TimeoutError, from-imported, shadows the builtin for bare names.
from mx import TimeoutError


def classify(e: Exception) -> str:
    if isinstance(e, TimeoutError):  # tpyc: ok
        return "mx"
    return "other"


def matched(e: Exception) -> str:
    match e:
        case TimeoutError():  # tpyc: ok
            return "mx"
        case _:
            return "other"


def run() -> None:
    try:
        raise TimeoutError()
    except Exception as e:
        print("shadow builtin:", classify(e), matched(e))
