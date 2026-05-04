# D16 Phase 4: __getattr__ cannot be @staticmethod.
from typing import Any

class Bag:
    @staticmethod
    def __getattr__(name: str) -> Any:  # tpyc: error(/cannot be a @staticmethod/)
        return None
