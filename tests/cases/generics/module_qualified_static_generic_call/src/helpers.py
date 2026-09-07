# Companion module: `main` calls this static generic as `helpers.Util.second`.
class Util:
    @staticmethod
    def second[T](a: T, b: T) -> T:
        return b
