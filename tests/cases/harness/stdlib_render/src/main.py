# Compiles every non-macro module under lib/tpy so the whole library's
# generated C++ is committed next to this case -- the render oracle that
# outlives the AST body emitter. options.json snapshots "*", so this import
# list alone decides what is covered; tests/test_stdlib_render_coverage.py
# asserts it stays equal to the set of non-macro modules under lib/tpy.
import _bindings
import _bindings.hinnant_date
import _bindings.mbedtls
import _bindings.pcre2
import _bindings.posix_epoll
import _bindings.posix_signal
import _bindings.posix_socket
import _bindings.posix_termios
import _bindings.tz_intern
import _datetime_cal
import _datetime_fmt
import _datetime_parse
import asyncio
import asyncio._executor
import base64
import bisect
import builtins
import collections
import csv
import datetime
import errno
import functools
import hashlib
import heapq
import http
import http.client
import io
import itertools
import json
import math
import os
import os._environ
import os._native
import os._types
import os.path
import random
import re
import signal
import socket
import ssl
import sys
import termios
import time
import tplib
import tplib.arc
import tplib.array_list
import tplib.box
import tplib.channel
import tplib.fix_str
import tplib.json
import tplib.json.parser
import tplib.json.writer
import tplib.rc
import tplib.requests
import tpy
import tpy._bootstrap
import tpy._bootstrap._decorators
import tpy._bootstrap._extern
import tpy._builtins
import tpy._builtins._bytes
import tpy._builtins._dict
import tpy._builtins._exceptions
import tpy._builtins._funcs
import tpy._builtins._io
import tpy._builtins._list
import tpy._builtins._range
import tpy._builtins._set
import tpy._builtins._super
import tpy._builtins._types
import tpy._core
import tpy._core._bytes_view
import tpy._core._containers
import tpy._core._functions
import tpy._core._types
import tpy._typing
import tpy.atomic
import tpy.bits
import tpy.channel
import tpy.coro
import tpy.extern
import tpy.mem
import tpy.sync
import tpy.thread
import tpy.unsafe
import tpy.version
import tty
import typing
import urllib
import urllib.parse
import urllib.request
import zoneinfo


# `itertools.cycle` is the library's one owning-slot copy at an open payload.
# Its hedge is declaration-time, so this call is not what produces it -- it is
# here so the render of an instantiated `cycle` is pinned alongside.
from tpy import int32


class _Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def _pin_cycle_copy() -> int32:
    total = 0
    for c in itertools.islice(itertools.cycle([_Cell(1), _Cell(2)]), 3):
        total += c.n
    return total
