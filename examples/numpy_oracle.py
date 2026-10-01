"""Capture what numpy computes, as the oracle for the `numpy` port.

    ../shallowflaws/.venv/bin/python examples/numpy_oracle.py

Each case is a few named input arrays and one expression over them in a
small call syntax — `sum(multiply(a, b), 1)` — that both this script and
`numpy::demo` evaluate: here with numpy 2.5.3 itself, there with the port.
The result is recorded bit for bit: its dtype, its shape, and every
element (floats as their IEEE bit patterns, any NaN as `nan`), or the
exception numpy raised, type and message.

The expression syntax: a name (`a`, `b`, …) is an input; `16` or `-1` is a
Python int and `f('3fe0000000000000')` a Python float given by its bits —
both *weak* under NEP 50, so they take the array's dtype; `True`, `False`,
`None`, and the dtype names `bool_`, `int64`, `float32`, `float64` mean
what they mean in Python. Every function is numpy's own, called
positionally; `sort` and `argsort` ask for `kind='stable'`, the only order
two implementations can agree on when elements tie.

The cases are chosen for what an implementation gets wrong without
noticing: the order numpy sums in (pairwise along the last axis, slice by
slice along any other), float32 arithmetic rounded once per operation,
NEP 50's promotion of Python scalars, NaN and signed zeros through
`maximum`, `clip`, reductions, and sorting, wraparound in int64, and the
exact text of every error. Inputs come from a fixed LCG, so the capture is
reproducible.
"""

import json
import os
import platform
import struct
import sys
import warnings

import numpy as np

warnings.simplefilter("ignore")

here = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Deterministic inputs.
# ---------------------------------------------------------------------------

class Lcg:
    def __init__(self, seed):
        self.x = seed

    def next(self):
        self.x = (self.x * 1103515245 + 12345) % 2147483648
        return self.x >> 16

    def unit(self):
        return self.next() / 32768.0


def floats(n, seed, spread=3):
    """`n` floats of mixed sign and magnitude, 10^-spread to 10^spread."""
    g = Lcg(seed)
    out = []
    for _ in range(n):
        mant = g.unit() * 2.0 - 1.0
        out.append(mant * 10.0 ** ((g.next() % (2 * spread + 1)) - spread))
    return out


def ints(n, seed, lo, hi):
    g = Lcg(seed)
    return [lo + g.next() % (hi - lo + 1) for _ in range(n)]


def arr(values, dtype, shape=None):
    a = np.array(values, dtype=dtype)
    return a if shape is None else a.reshape(shape)


NAN = float("nan")
INF = float("inf")
SPECIAL = [0.0, -0.0, 1.0, -1.0, 0.5, NAN, INF, -INF, 1e-45, 5e-324, 3.4028234663852886e38,
           1.7976931348623157e308, 1e-40, 2.5, -2.5, 3.5, 0.1, 16777217.0, -7.0, 1e30]


# ---------------------------------------------------------------------------
# The expression language, evaluated by numpy.
# ---------------------------------------------------------------------------

def f(bits):
    return struct.unpack(">d", bytes.fromhex(bits))[0]


def fx(x):
    """A Python float literal for the expression syntax."""
    return "f('%s')" % struct.pack(">d", x).hex()


NS = {
    "f": f,
    "True": True, "False": False, "None": None,
    "bool_": np.bool_, "int64": np.int64, "float32": np.float32, "float64": np.float64,
    "arange": lambda n: np.arange(n),
    "reshape": lambda x, *shape: np.reshape(x, shape),
    "expand_dims": lambda x, axis: np.expand_dims(x, axis),
    "squeeze": lambda x, axis=None: np.squeeze(x, axis),
    "ravel": lambda x: np.ravel(x),
    "astype": lambda x, t: np.asarray(x).astype(t),
    "add": np.add, "subtract": np.subtract, "multiply": np.multiply, "divide": np.divide,
    "maximum": np.maximum, "minimum": np.minimum,
    "equal": np.equal, "not_equal": np.not_equal, "less": np.less,
    "less_equal": np.less_equal, "greater": np.greater, "greater_equal": np.greater_equal,
    "negative": np.negative, "absolute": np.absolute, "sqrt": np.sqrt, "square": np.square,
    "rint": np.rint, "floor": np.floor, "ceil": np.ceil, "isnan": np.isnan,
    "where": np.where,
    "clip": np.clip,
    "round": lambda x, decimals=0: np.round(x, decimals),
    "sum": lambda x, axis=None, keepdims=False: np.sum(x, axis=axis, keepdims=keepdims),
    "mean": lambda x, axis=None, keepdims=False: np.mean(x, axis=axis, keepdims=keepdims),
    "max": lambda x, axis=None, keepdims=False: np.max(x, axis=axis, keepdims=keepdims),
    "min": lambda x, axis=None, keepdims=False: np.min(x, axis=axis, keepdims=keepdims),
    "argmax": lambda x, axis=None, keepdims=False: np.argmax(x, axis=axis, keepdims=keepdims),
    "argmin": lambda x, axis=None, keepdims=False: np.argmin(x, axis=axis, keepdims=keepdims),
    "sort": lambda x, axis=-1: np.sort(x, axis=axis, kind="stable"),
    "argsort": lambda x, axis=-1: np.argsort(x, axis=axis, kind="stable"),
    "take": lambda x, idx, axis=None: np.take(x, idx, axis=axis),
    "take_along_axis": lambda x, idx, axis: np.take_along_axis(x, idx, axis=axis),
}

DTYPES = {np.dtype(np.bool_): "bool", np.dtype(np.int64): "int64",
          np.dtype(np.float32): "float32", np.dtype(np.float64): "float64"}


def element(dtype, v):
    if dtype == "bool":
        return "1" if v else "0"
    if dtype == "int64":
        return str(int(v))
    if np.isnan(v):
        return "nan"
    if dtype == "float32":
        return struct.pack(">f", v).hex()
    return struct.pack(">d", v).hex()


def render(a, raw_nan=False):
    """`dtype|shape|elements`, the form `numpy::demo` renders too."""
    a = np.asarray(a)
    dtype = DTYPES[a.dtype]
    out = []
    for v in a.ravel():
        if raw_nan and dtype in ("float32", "float64"):
            out.append(struct.pack(">f" if dtype == "float32" else ">d", v).hex())
        else:
            out.append(element(dtype, v))
    return "%s|%s|%s" % (dtype, ",".join(str(d) for d in a.shape), " ".join(out))


def run(inputs, expr):
    env = dict(NS)
    env.update(inputs)
    try:
        return render(eval(expr, {"__builtins__": {}}, env))
    except Exception as e:  # noqa: BLE001 — every exception is an outcome
        return "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------------------
# The cases.
# ---------------------------------------------------------------------------

cases = []


def case(group, name, inputs, expr):
    cases.append({
        "group": group,
        "name": name,
        "inputs": [[k, render(v, raw_nan=True)] for k, v in inputs.items()],
        "expr": expr,
        "result": run(inputs, expr),
    })


f32, f64, i64, b8 = np.float32, np.float64, np.int64, np.bool_

# --- summation order --------------------------------------------------------
# A sum along the last axis is pairwise: runs under eight elements are added
# in turn from -0.0, runs up to 128 through eight accumulators, longer runs
# split in two at a multiple of eight. Lengths straddle every boundary.
for dt in (f32, f64):
    name = DTYPES[np.dtype(dt)]
    for n in (1, 2, 7, 8, 9, 15, 16, 17, 31, 127, 128, 129, 130, 255, 256, 257, 1000, 1031):
        case("sum", "%s sum of %d" % (name, n), {"a": arr(floats(n, 100 + n), dt)}, "sum(a)")
    for shape in ((3, 5), (5, 3), (200, 7), (7, 200), (9, 130), (2, 3, 4), (4, 33, 10), (17, 9, 13)):
        x = arr(floats(int(np.prod(shape)), 7 * sum(shape)), dt, shape)
        for axis in [None] + list(range(len(shape))) + [-1]:
            case("sum", "%s sum of %s along %s" % (name, shape, axis), {"a": x},
                 "sum(a)" if axis is None else "sum(a, %d)" % axis)
    case("sum", "%s sum keeps dims" % name, {"a": arr(floats(24, 9), dt, (2, 3, 4))}, "sum(a, 1, True)")
    case("sum", "%s sum of nothing" % name, {"a": arr([], dt)}, "sum(a)")
    case("sum", "%s sum of an empty axis" % name, {"a": arr([], dt, (3, 0))}, "sum(a, 1)")
    case("sum", "%s sum along a zero-length outer axis" % name, {"a": arr([], dt, (0, 4))}, "sum(a, 0)")
    case("sum", "%s sum with infinities and NaN" % name, {"a": arr([1.0, INF, -INF, 2.0], dt)}, "sum(a)")
    case("sum", "%s sum of signed zeros" % name, {"a": arr([-0.0, -0.0], dt)}, "sum(a)")
    case("sum", "%s sum of a zero" % name, {"a": arr([-0.0], dt)}, "sum(a)")
    case("sum", "%s cancellation" % name, {"a": arr([1e8, 1.0, -1e8, 3.0] * 5, dt)}, "sum(a)")
    for n in (1, 9, 130):
        case("sum", "%s mean of %d" % (name, n), {"a": arr(floats(n, 300 + n), dt)}, "mean(a)")
    case("sum", "%s mean along each axis" % name, {"a": arr(floats(60, 31), dt, (3, 4, 5))}, "mean(a, 1)")
    case("sum", "%s mean along the last axis" % name, {"a": arr(floats(60, 32), dt, (3, 4, 5))}, "mean(a, -1, True)")
    case("sum", "%s mean of nothing" % name, {"a": arr([], dt)}, "mean(a)")
for dt in (f32, f64):
    name = DTYPES[np.dtype(dt)]
    case("sum", "%s sum along an axis followed by length-one axes" % name,
         {"a": arr(floats(60, 33), dt, (3, 20, 1))}, "sum(a, 1)")
    case("sum", "%s sum along the first of two axes, the second of length one" % name,
         {"a": arr(floats(40, 34), dt, (40, 1))}, "sum(a, 0)")
    case("sum", "%s sum along a long axis between two others" % name,
         {"a": arr(floats(2 * 150 * 3, 35), dt, (2, 150, 3))}, "sum(a, 1)")
case("sum", "int64 sum", {"a": arr(ints(50, 5, -10 ** 6, 10 ** 6), i64, (5, 10))}, "sum(a, 0)")
case("sum", "int64 sum wraps", {"a": arr([2 ** 62, 2 ** 62, 2 ** 62, 7], i64)}, "sum(a)")
case("sum", "int64 mean", {"a": arr(ints(30, 6, -1000, 1000), i64, (3, 10))}, "mean(a, 1)")
case("sum", "bool sum counts", {"a": arr([True, False, True, True], b8)}, "sum(a)")
case("sum", "bool mean", {"a": arr([True, False, True, True], b8)}, "mean(a)")

# --- elementwise arithmetic and float32 rounding -----------------------------
for dt in (f32, f64):
    name = DTYPES[np.dtype(dt)]
    a = arr(floats(40, 11, 6), dt, (5, 8))
    b = arr(floats(40, 12, 6), dt, (5, 8))
    for op in ("add", "subtract", "multiply", "divide", "maximum", "minimum"):
        case("arith", "%s %s" % (name, op), {"a": a, "b": b}, "%s(a, b)" % op)
    s = arr(SPECIAL, dt)
    t = arr(SPECIAL[::-1], dt)
    for op in ("add", "subtract", "multiply", "divide", "maximum", "minimum"):
        case("arith", "%s %s of special values" % (name, op), {"a": s, "b": t}, "%s(a, b)" % op)
    for op in ("negative", "absolute", "sqrt", "square", "rint", "floor", "ceil", "isnan"):
        case("arith", "%s %s" % (name, op), {"a": s}, "%s(a)" % op)
        case("arith", "%s %s of ordinary values" % (name, op), {"a": a}, "%s(a)" % op)
    case("arith", "%s sqrt of exact squares and their neighbours" % name,
         {"a": arr([4.0, 9.0, 2.0, 3.0, 1e-300 if dt == f64 else 1e-38, 6.25, 0.01, 1e10, 123456789.0, 2.0 ** -1074 if dt == f64 else 2.0 ** -149], dt)},
         "sqrt(a)")
    case("arith", "%s rint ties to even" % name,
         {"a": arr([0.5, 1.5, 2.5, -0.5, -1.5, -2.5, 4503599627370497.0, 8388609.0, 0.49999997, -0.0], dt)}, "rint(a)")
    case("arith", "%s floor and ceil at the edges" % name,
         {"a": arr([-0.5, 0.5, -1.0, 1e300 if dt == f64 else 1e30, -1e300 if dt == f64 else -1e30, -0.0, 2.0 ** 60, -(2.0 ** 60) - 2.0 ** 8], dt)},
         "add(floor(a), ceil(a))")
    case("arith", "%s overflow to infinity" % name, {"a": arr([1e38, 3e38, -3e38, 1e308], dt)}, "multiply(a, 10)")
    case("arith", "%s underflow to subnormals and zero" % name, {"a": arr([1e-38, 1e-40, 1e-308, 1e-320, -1e-45], dt)}, "divide(a, 7)")
case("arith", "float32 0.1 + 0.2", {"a": arr([0.1], f32), "b": arr([0.2], f32)}, "add(a, b)")
case("arith", "float32 rounding of 1/3", {"a": arr([1.0, 2.0, 10.0], f32)}, "divide(a, 3)")
case("arith", "int64 arithmetic", {"a": arr(ints(12, 21, -100, 100), i64, (3, 4)), "b": arr(ints(12, 22, 1, 50), i64, (3, 4))},
     "add(multiply(a, b), subtract(b, a))")
case("arith", "int64 wraps on overflow",
     {"a": arr([2 ** 63 - 1, -2 ** 63, 2 ** 62, 3037000500, -2 ** 63, 12345678901], i64)},
     "add(multiply(a, a), a)")
case("arith", "int64 negative and absolute of the minimum", {"a": arr([-2 ** 63, -5, 0, 7], i64)}, "add(negative(a), absolute(a))")
case("arith", "int64 divide is true division", {"a": arr([7, -7, 1, 0, 9007199254740993], i64), "b": arr([2, 2, 0, 0, 1], i64)}, "divide(a, b)")
case("arith", "int64 sqrt is float64", {"a": arr([0, 1, 2, 4, 10 ** 18], i64)}, "sqrt(a)")
case("arith", "int64 maximum and minimum", {"a": arr([3, -8, 5], i64), "b": arr([4, -9, 5], i64)}, "subtract(maximum(a, b), minimum(a, b))")
case("arith", "bool add is or, multiply is and",
     {"a": arr([True, True, False, False], b8), "b": arr([True, False, True, False], b8)}, "add(a, b)")
case("arith", "bool multiply", {"a": arr([True, True, False, False], b8), "b": arr([True, False, True, False], b8)}, "multiply(a, b)")
case("arith", "bool divide is float64", {"a": arr([True, True, False], b8), "b": arr([True, False, False], b8)}, "divide(a, b)")
case("arith", "bool subtract is refused", {"a": arr([True], b8), "b": arr([False], b8)}, "subtract(a, b)")
case("arith", "bool negative is refused", {"a": arr([True], b8)}, "negative(a)")
case("arith", "bool absolute", {"a": arr([True, False], b8)}, "absolute(a)")
case("arith", "bool isnan", {"a": arr([True, False], b8)}, "isnan(a)")
case("arith", "int64 rint is float64", {"a": arr([3, -4], i64)}, "rint(a)")
case("arith", "int64 floor stays int64", {"a": arr([3, -4], i64)}, "floor(a)")
case("arith", "int64 isnan", {"a": arr([3, -4], i64)}, "isnan(a)")

# --- dtype promotion, NEP 50 -------------------------------------------------
ARRS = {
    "bool": arr([True, False, True], b8),
    "int64": arr([3, -2, 2 ** 40 + 1], i64),
    "float32": arr([0.1, -2.5, 16777217.0], f32),
    "float64": arr([0.1, -2.5, 1e300], f64),
}
for ln, la in ARRS.items():
    for rn, ra in ARRS.items():
        case("promote", "%s + %s" % (ln, rn), {"a": la, "b": ra}, "add(a, b)")
    case("promote", "%s + a Python int" % ln, {"a": la}, "add(a, 3)")
    case("promote", "%s + a Python float" % ln, {"a": la}, "add(a, %s)" % fx(0.1))
    case("promote", "%s compared with a Python float" % ln, {"a": la}, "less(a, %s)" % fx(0.1))
case("promote", "float32 times a float that rounds", {"a": arr([3.0, 1.0], f32)}, "multiply(a, %s)" % fx(0.1))
case("promote", "two Python scalars", {}, "add(2, %s)" % fx(0.5))
case("promote", "a Python int alone", {}, "multiply(3, 4)")
case("promote", "a Python float alone", {}, "sqrt(%s)" % fx(2.0))
case("promote", "an int64 scalar from a reduction is strong", {"a": arr([1, 2], i64), "b": arr([0.5], f32)}, "add(b, sum(a))")
case("promote", "a float32 scalar from a reduction is strong", {"a": arr([1.5], f32), "b": arr([2], i64)}, "add(b, sum(a))")
case("promote", "int64 compared with float32 goes through float64",
     {"a": arr([16777217], i64), "b": arr([16777216.0], f32)}, "equal(a, b)")
for cmp in ("equal", "not_equal", "less", "less_equal", "greater", "greater_equal"):
    case("promote", "%s with NaN and signed zeros" % cmp,
         {"a": arr([0.0, -0.0, NAN, 1.0, NAN], f32), "b": arr([-0.0, 0.0, NAN, NAN, 1.0], f32)}, "%s(a, b)" % cmp)
case("promote", "astype float64 to float32 rounds once",
     {"a": arr([0.1, 1e300, -1e-300, 16777219.0, 3.4028235677973366e38, NAN], f64)}, "astype(a, float32)")
case("promote", "astype int64 to float32 rounds once",
     {"a": arr([16777217, 16777219, 2 ** 53 + 1, 2 ** 62 + 2 ** 38 + 1, -(2 ** 63), 9007199791611905], i64)}, "astype(a, float32)")
case("promote", "astype int64 to float64", {"a": arr([2 ** 53 + 1, -(2 ** 63), 2 ** 63 - 1], i64)}, "astype(a, float64)")
case("promote", "astype float to int64 truncates", {"a": arr([2.9, -2.9, -0.0, 1e15 + 0.5], f64)}, "astype(a, int64)")
case("promote", "astype to bool", {"a": arr([0.0, -0.0, 0.5, NAN], f32)}, "astype(a, bool_)")
case("promote", "astype bool to float32", {"a": arr([True, False], b8)}, "astype(a, float32)")

# --- broadcasting and shapes ----------------------------------------------------
x23 = arr(floats(6, 41), f32, (2, 3))
case("broadcast", "row against matrix", {"a": x23, "b": arr([1.0, 2.0, 3.0], f32)}, "multiply(a, b)")
case("broadcast", "column against matrix", {"a": x23, "b": arr([10.0, 20.0], f32, (2, 1))}, "add(a, b)")
case("broadcast", "column against row", {"a": arr([1.0, 2.0, 3.0], f64, (3, 1)), "b": arr([1.0, 2.0], f64)}, "subtract(a, b)")
case("broadcast", "three dimensions", {"a": arr(floats(24, 42), f32, (2, 3, 4)), "b": arr(floats(3, 43), f32, (3, 1))}, "add(a, b)")
case("broadcast", "a 0-d array", {"a": x23, "b": arr(2.0, f32)}, "multiply(a, b)")
case("broadcast", "empty against one", {"a": arr([], f32, (0, 3)), "b": arr([1.0], f32)}, "add(a, b)")
case("broadcast", "shapes that do not broadcast", {"a": x23, "b": arr([1.0, 2.0, 3.0, 4.0], f32)}, "add(a, b)")
case("broadcast", "shapes that do not broadcast, three dimensions",
     {"a": arr(floats(24, 44), f32, (2, 3, 4)), "b": arr(floats(6, 45), f32, (2, 3))}, "add(a, b)")
case("broadcast", "where broadcasts all three",
     {"c": arr([True, False, True], b8), "a": arr([1.0, 2.0], f32, (2, 1)), "b": arr(0.5, f32)}, "where(c, a, b)")
case("broadcast", "where that does not broadcast",
     {"c": arr([True, False, True], b8), "a": arr([1.0, 2.0], f32), "b": arr(0.5, f32)}, "where(c, a, b)")
case("broadcast", "where with Python scalars is float64", {"c": arr([True, False], b8)}, "where(c, %s, %s)" % (fx(2.0), fx(-2.0)))
case("broadcast", "where keeps float32 against a Python float",
     {"c": arr([True, False], b8), "a": arr([0.1, 0.2], f32)}, "where(c, a, %s)" % fx(1.0))
case("broadcast", "where on int64 and float32", {"c": arr([True, False], b8), "a": arr([1, 2], i64), "b": arr([0.5, 0.25], f32)}, "where(c, a, b)")
case("shape", "reshape", {"a": arr(floats(12, 46), f64)}, "reshape(a, 3, 4)")
case("shape", "reshape with -1", {"a": arr(floats(12, 46), f64)}, "reshape(a, 2, -1, 3)")
case("shape", "reshape to 0-d", {"a": arr([5.0], f32)}, "reshape(a)")
case("shape", "reshape that does not fit", {"a": arr(floats(6, 47), f64)}, "reshape(a, 4)")
case("shape", "reshape that does not fit, two dimensions", {"a": arr(floats(6, 47), f64)}, "reshape(a, 4, 2)")
case("shape", "reshape with two unknowns", {"a": arr(floats(6, 47), f64)}, "reshape(a, -1, -1)")
case("shape", "reshape with -1 that does not divide", {"a": arr(floats(6, 47), f64)}, "reshape(a, 4, -1)")
case("shape", "expand_dims at the front", {"a": x23}, "expand_dims(a, 0)")
case("shape", "expand_dims at the back", {"a": x23}, "expand_dims(a, -1)")
case("shape", "expand_dims out of bounds", {"a": x23}, "expand_dims(a, 3)")
case("shape", "squeeze everything", {"a": arr(floats(3, 48), f32, (1, 3, 1))}, "squeeze(a)")
case("shape", "squeeze one axis", {"a": arr(floats(3, 48), f32, (1, 3, 1))}, "squeeze(a, 2)")
case("shape", "squeeze an axis that is not one", {"a": arr(floats(3, 48), f32, (1, 3, 1))}, "squeeze(a, 1)")
case("shape", "ravel", {"a": arr(floats(24, 49), f32, (2, 3, 4))}, "ravel(a)")
case("shape", "an axis out of bounds", {"a": x23}, "sum(a, 2)")
case("shape", "a negative axis out of bounds", {"a": x23}, "max(a, -3)")
case("shape", "arange", {}, "arange(5)")

# --- where, clip, round ------------------------------------------------------------
case("select", "where on NaN and signed zeros",
     {"c": arr([True, False, True, False], b8), "a": arr([NAN, 1.0, -0.0, 2.0], f32), "b": arr([3.0, NAN, 4.0, 0.0], f32)}, "where(c, a, b)")
case("select", "where with a float condition goes by truth",
     {"c": arr([1.0, 0.0, -0.0, NAN], f32), "a": arr([1.0, 2.0, 3.0, 4.0], f32), "b": arr([5.0, 6.0, 7.0, 8.0], f32)}, "where(c, a, b)")
case("select", "where with an int64 condition", {"c": arr([0, 3, -1], i64), "a": arr([1, 2, 3], i64)}, "where(c, a, 0)")
for dt in (f32, f64):
    name = DTYPES[np.dtype(dt)]
    case("select", "%s clip" % name, {"a": arr(floats(30, 51), dt)}, "clip(a, %s, %s)" % (fx(-0.5), fx(2.0)))
    case("select", "%s clip with NaN and signed zeros" % name,
         {"a": arr([NAN, -0.0, 0.0, -1.0, 5.0, INF], dt)}, "clip(a, %s, %s)" % (fx(0.0), fx(1.0)))
    case("select", "%s clip with a NaN bound" % name, {"a": arr([1.0, 2.0], dt)}, "clip(a, %s, %s)" % (fx(NAN), fx(1.5)))
    case("select", "%s clip with no upper bound" % name, {"a": arr([1e-9, 0.0, -3.0, 7.0], dt)}, "clip(a, %s, None)" % fx(1e-8))
    case("select", "%s clip with no lower bound" % name, {"a": arr([1e-9, 0.0, -3.0, 7.0], dt)}, "clip(a, None, %s)" % fx(1.0))
    case("select", "%s clip with crossed bounds" % name, {"a": arr([0.0, 5.0, 10.0], dt)}, "clip(a, %s, %s)" % (fx(6.0), fx(4.0)))
    case("select", "%s clip with array bounds" % name,
         {"a": arr(floats(6, 52), dt, (2, 3)), "b": arr([-0.1, 0.0, 0.1], dt), "c": arr([0.2], dt)}, "clip(a, b, c)")
    for d in (0, 1, 2, 4, -1, -2):
        case("select", "%s round to %d" % (name, d), {"a": arr(floats(30, 60 + d, 4) + [0.125, 0.375, 2.5, -2.5, 0.005, 1.005, 2.675, -0.0], dt)},
             "round(a, %d)" % d)
case("select", "int64 clip", {"a": arr([-5, 0, 5, 10], i64)}, "clip(a, 0, 7)")
case("select", "int64 clip to a float", {"a": arr([-5, 0, 5, 10], i64)}, "clip(a, %s, 7)" % fx(0.5))
case("select", "int64 round is a copy", {"a": arr([15, -25], i64)}, "round(a, 1)")
case("select", "int64 round to tens", {"a": arr([15, -25, 35, 1234, -9999], i64)}, "round(a, -1)")
case("select", "clip with no bounds", {"a": arr([1.0, -2.0], f32)}, "clip(a, None, None)")

# --- reductions: max, min, argmax, argmin -----------------------------------------
for dt in (f32, f64):
    name = DTYPES[np.dtype(dt)]
    x = arr(floats(60, 71), dt, (3, 4, 5))
    for fn in ("max", "min", "argmax", "argmin"):
        case("extrema", "%s %s" % (name, fn), {"a": x}, "%s(a)" % fn)
        for axis in (0, 1, 2):
            case("extrema", "%s %s along %d" % (name, fn, axis), {"a": x}, "%s(a, %d)" % (fn, axis))
        case("extrema", "%s %s keeps dims" % (name, fn), {"a": x}, "%s(a, 1, True)" % fn)
        case("extrema", "%s %s with NaN" % (name, fn), {"a": arr([1.0, NAN, 3.0, NAN, -1.0], dt)}, "%s(a)" % fn)
        case("extrema", "%s %s of signed zeros" % (name, fn), {"a": arr([-0.0, 0.0, -0.0], dt)}, "%s(a)" % fn)
        case("extrema", "%s %s of nothing" % (name, fn), {"a": arr([], dt)}, "%s(a)" % fn)
        case("extrema", "%s %s along an empty axis" % (name, fn), {"a": arr([], dt, (2, 0))}, "%s(a, 1)" % fn)
        case("extrema", "%s %s with ties" % (name, fn), {"a": arr([2.0, 5.0, 5.0, -1.0, -1.0], dt)}, "%s(a)" % fn)
    case("extrema", "%s max with NaN along an axis" % name, {"a": arr([1.0, NAN, 3.0, 4.0, 5.0, NAN], dt, (2, 3))}, "max(a, 0)")
for fn in ("max", "min", "argmax", "argmin"):
    case("extrema", "%s with nothing to reduce but nothing to return" % fn, {"a": arr([], f32, (0, 4))}, "%s(a, 1)" % fn)
    case("extrema", "%s along an empty axis with nothing to return" % fn, {"a": arr([], f32, (0, 4))}, "%s(a, 0)" % fn)
    case("extrema", "int64 %s" % fn, {"a": arr(ints(20, 81, -50, 50), i64, (4, 5))}, "%s(a, 1)" % fn)
    case("extrema", "bool %s" % fn, {"a": arr([False, True, True, False], b8)}, "%s(a)" % fn)

# --- sorting and taking ---------------------------------------------------------------
for dt in (f32, f64):
    name = DTYPES[np.dtype(dt)]
    x = arr(floats(40, 91), dt, (5, 8))
    for fn in ("sort", "argsort"):
        case("sort", "%s %s" % (name, fn), {"a": x}, "%s(a)" % fn)
        case("sort", "%s %s along 0" % (name, fn), {"a": x}, "%s(a, 0)" % fn)
        case("sort", "%s %s flattened" % (name, fn), {"a": x}, "%s(a, None)" % fn)
        case("sort", "%s %s with NaN, infinities, and signed zeros" % (name, fn),
             {"a": arr([NAN, 0.0, -INF, -0.0, 1.0, NAN, INF, 0.0, -0.0, -1.0], dt)}, "%s(a)" % fn)
        case("sort", "%s %s with ties" % (name, fn), {"a": arr([3.0, 1.0, 3.0, 2.0, 1.0, 3.0] * 7, dt)}, "%s(a)" % fn)
    case("sort", "%s descending by negation" % name, {"a": arr([0.3, 0.1, 0.3, 0.2], dt)}, "argsort(negative(a))")
case("sort", "int64 argsort", {"a": arr(ints(30, 92, -5, 5), i64, (3, 10))}, "argsort(a)")
case("sort", "bool sort", {"a": arr([True, False, True, False], b8)}, "sort(a)")
case("sort", "sort a 0-d array", {"a": arr(1.0, f32)}, "sort(a)")
case("sort", "sort along an axis out of bounds", {"a": x23}, "sort(a, 2)")
case("sort", "take along the last axis", {"a": x23, "i": arr([[2, 0], [1, 1]], i64)}, "take_along_axis(a, i, 1)")
case("sort", "take along the first axis", {"a": x23, "i": arr([[1, 0, 1]], i64)}, "take_along_axis(a, i, 0)")
case("sort", "take along with negative indices", {"a": x23, "i": arr([[-1], [-3]], i64)}, "take_along_axis(a, i, 1)")
case("sort", "take along out of bounds", {"a": x23, "i": arr([[3], [0]], i64)}, "take_along_axis(a, i, 1)")
case("sort", "take along with the wrong number of dimensions", {"a": x23, "i": arr([1, 0], i64)}, "take_along_axis(a, i, 1)")
case("sort", "take along with float indices", {"a": x23, "i": arr([[1.0], [0.0]], f64)}, "take_along_axis(a, i, 1)")
case("sort", "take with float indices", {"a": x23, "i": arr([1.0], f64)}, "take(a, i)")
case("sort", "take flattened", {"a": x23, "i": arr([5, 0, -1], i64)}, "take(a, i)")
case("sort", "take along an axis", {"a": x23, "i": arr([2, 2, 0], i64)}, "take(a, i, 1)")
case("sort", "take a 2-d index", {"a": arr(floats(5, 93), f32), "i": arr([[0, 1], [4, 3]], i64)}, "take(a, i, 0)")
case("sort", "take out of bounds", {"a": x23}, "take(a, arange(7))")
case("sort", "take out of bounds along an axis", {"a": x23}, "take(a, arange(4), 1)")
case("sort", "the five smallest, as torch.topk(largest=False) finds them",
     {"a": arr(floats(24, 94), f32, (3, 8))}, "take_along_axis(a, take(argsort(a), arange(5), 1), 1)")

# --- the questionnaire score, in numpy ----------------------------------------------
# What app/core/matching.py computes with torch, written with these functions:
# multiple-choice questions mapped to ±2 with weight 1, weighted Euclidean
# distance to the employer's answers, normalised by the largest possible
# distance, and the per-question impact the top five are picked from.
Q, N = 12, 6
emp = arr(ints(Q, 101, -2, 2), f32)
empw = arr([[0.5, 1.0, 2.0][v] for v in ints(Q, 102, 0, 2)], f32)
mc = arr([v == 0 for v in ints(Q, 103, 0, 3)], b8)
apps = arr(ints(N * Q, 104, -2, 2), f32, (N, Q))
appw = arr([[0.5, 1.0, 2.0][v] for v in ints(N * Q, 105, 0, 2)], f32, (N, Q))
inputs = {"e": emp, "w": empw, "m": mc, "a": apps, "v": appw}
two, minus_two, one, sixteen = fx(2.0), fx(-2.0), fx(1.0), fx(16.0)
emp_vec = "where(m, %s, e)" % two
emp_w = "where(m, %s, w)" % one
app_mat = "where(m, where(equal(a, e), astype(%s, float32), astype(%s, float32)), a)" % (two, minus_two)
app_w = "where(m, %s, v)" % one
combined = "multiply(%s, %s)" % (emp_w, app_w)
# As matching.py has it: the applicants scaled by the combined weights, the
# employer by the employer's own.
dist = "sqrt(sum(square(subtract(multiply(%s, sqrt(%s)), multiply(%s, sqrt(%s)))), 1))" % (app_mat, combined, emp_vec, emp_w)
max_dist = "sqrt(sum(multiply(%s, %s), 1))" % (combined, sixteen)
score = "clip(multiply(subtract(%s, divide(%s, clip(%s, %s, None))), %s), %s, %s)" % (
    one, dist, max_dist, fx(1e-8), fx(100.0), fx(0.0), fx(100.0))
impact = "multiply(divide(absolute(subtract(%s, %s)), %s), %s)" % (app_mat, emp_vec, fx(4.0), combined)
case("matching", "the employer's vector", inputs, emp_vec)
case("matching", "the applicants' matrix", inputs, app_mat)
case("matching", "the combined weights", inputs, combined)
case("matching", "the weighted distances", inputs, dist)
case("matching", "the largest distances", inputs, max_dist)
case("matching", "the scores", inputs, score)
case("matching", "the scores, rounded to two places", inputs, "round(%s, 2)" % score)
case("matching", "the per-question impact", inputs, impact)
case("matching", "the five smallest impacts", inputs, "take(argsort(%s), arange(5), 1)" % impact)
case("matching", "the five largest impacts", inputs, "take(argsort(negative(%s)), arange(5), 1)" % impact)
case("matching", "the ranking", inputs, "argsort(negative(%s))" % score)


out = {
    "numpy": np.__version__,
    "python": platform.python_version(),
    "machine": "%s %s" % (platform.system(), platform.machine()),
    "cases": cases,
}
with open(os.path.join(here, "numpy_oracle.json"), "w") as fh:
    json.dump(out, fh, indent=1)
    fh.write("\n")
print("%d cases (numpy %s, CPython %s, %s)" % (len(cases), out["numpy"], out["python"], out["machine"]), file=sys.stderr)
