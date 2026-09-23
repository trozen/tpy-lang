# Re-exports tones.Shade: a call through `palette.Shade` must still reach the
# companion in the module that declares it.
from tones import Shade

__all__ = ["Shade"]
