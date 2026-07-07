# tplib -- TurboPython standard library
# tpy: cpp_namespace("tpystd::tplib")
from tplib.box import Box
# Weak is intentionally NOT re-exported here. It lives at tplib.rc.Weak so
# that the future Arc[T] companion (`tplib.arc.Weak`) can take the same
# bare-`Weak` name without collision -- mirrors std::rc::Weak vs
# std::sync::Weak in Rust. Users import it as `from tplib.rc import Weak`.
from tplib.rc import Rc
# Arc[T] / Weak[T] (atomic sibling of Rc) are intentionally NOT re-exported here.
# Re-exporting would pull the atomic runtime (tpy.atomic -> <atomic>) into every
# `from tplib import ...` consumer, even non-threaded ones -- against the "pay
# nothing if you don't use threads" principle. Import as
# `from tplib.arc import Arc, Weak`.
from tplib.array_list import ArrayList
from tplib.fix_str import FixStr
