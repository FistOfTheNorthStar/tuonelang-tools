"""The workloads of examples/numpy_bench.tuo, timed with numpy — and the
questionnaire matching also with the backend's own torch function.

    ../shallowflaws/.venv/bin/python examples/numpy_bench.py

Same inputs (the oracle's LCG), same output: the best of several runs in
microseconds, and a checksum of the result to compare with the port's.
"""

import importlib.util
import os
import struct
import sys
import time
import warnings

import numpy as np

warnings.simplefilter("ignore")
here = os.path.dirname(os.path.abspath(__file__))


class Lcg:
    def __init__(self, seed):
        self.x = seed

    def next(self):
        self.x = (self.x * 1103515245 + 12345) % 2147483648
        return self.x >> 16


def floats(n, seed, dtype):
    g = Lcg(seed)
    return np.array([g.next() / 32768.0 * 2.0 - 1.0 for _ in range(n)], dtype=dtype)


def choices(n, seed, table):
    g = Lcg(seed)
    return [table[g.next() % len(table)] for _ in range(n)]


def check(v):
    v = np.asarray(v)
    if v.dtype == np.float32:
        return "nan" if np.isnan(v) else struct.pack(">f", v).hex()
    if v.dtype == np.float64:
        return "nan" if np.isnan(v) else struct.pack(">d", v).hex()
    return str(int(v))


def best(fn, reps):
    out, t = None, float("inf")
    for _ in range(reps):
        t0 = time.perf_counter_ns()
        out = fn()
        t = min(t, time.perf_counter_ns() - t0)
    return t / 1000.0, out


def report(name, micros, chk):
    print("%s\t%.1f\t%s" % (name, micros, chk))


def matching_numpy(e, w, m, a, v):
    emp_vec = np.where(m, 2.0, e)
    emp_w = np.where(m, 1.0, w)
    expanded = np.where(np.equal(a, e), np.float32(2.0), np.float32(-2.0))
    app = np.where(m, expanded, a)
    app_w = np.where(m, 1.0, v)
    combined = emp_w * app_w
    diff = app * np.sqrt(combined) - emp_vec * np.sqrt(emp_w)
    dist = np.sqrt(np.sum(np.square(diff), 1))
    max_dist = np.sqrt(np.sum(combined * 16.0, 1))
    scores = np.clip((1.0 - dist / np.clip(max_dist, 1e-8, None)) * 100.0, 0.0, 100.0)
    impact = np.abs(app - emp_vec) / 4.0 * combined
    np.take(np.argsort(impact, kind="stable"), np.arange(5), 1)
    np.take(np.argsort(-impact, kind="stable"), np.arange(5), 1)
    return scores


def bench_matching(name, n, q, reps, torch_matching):
    answers = [-2.0, -1.0, 0.0, 1.0, 2.0]
    weights = [0.5, 1.0, 2.0]
    e = np.array(choices(q, 101, answers), np.float32)
    w = np.array(choices(q, 102, weights), np.float32)
    g = Lcg(103)
    m = np.array([g.next() % 4 == 0 for _ in range(q)])
    a = np.array(choices(n * q, 104, answers), np.float32).reshape(n, q)
    v = np.array(choices(n * q, 105, weights), np.float32).reshape(n, q)
    micros, scores = best(lambda: matching_numpy(e, w, m, a, v), reps)
    report(name + " (numpy)", micros, check(scores[-1]))
    if torch_matching is None:
        return
    # The backend's function takes plain lists, as match_tasks.py passes them.
    emp = [int(x) for x in e]
    mco = [["x", "y"] if f else None for f in m]
    applicants = [(i, [int(x) for x in a[i]], [float(x) for x in v[i]]) for i in range(n)]
    micros, res = best(lambda: torch_matching(emp, [float(x) for x in w], mco, applicants), reps)
    last = [r for r in res if r.job_application_id == n - 1][0]
    report(name + " (torch, matching.py)", micros, "%.2f" % last.score)


def main():
    m1 = floats(1_000_000, 1, np.float32)
    m2 = floats(1_000_000, 2, np.float32)
    t, out = best(lambda: np.sum(m1), 20)
    report("sum float32 [1e6]", t, check(out))
    t, out = best(lambda: np.add(m1, m2), 20)
    report("add float32 [1e6] + [1e6]", t, check(out[-1]))
    t, out = best(lambda: np.sqrt(m1), 20)
    report("sqrt float32 [1e6]", t, check(out[-1]))
    matrix = floats(1_000_000, 3, np.float64).reshape(1000, 1000)
    row = floats(1000, 4, np.float64)
    t, out = best(lambda: np.multiply(matrix, row), 20)
    report("multiply float64 [1000,1000] * [1000]", t, check(out.ravel()[-1]))
    sq = floats(1_000_000, 5, np.float32).reshape(1000, 1000)
    t, out = best(lambda: np.sum(sq, 0), 20)
    report("sum float32 [1000,1000] axis 0", t, check(out[-1]))
    many = floats(100_000, 6, np.float64)
    t, out = best(lambda: np.argsort(many, kind="stable"), 20)
    report("argsort float64 [1e5]", t, check(out[0]))

    torch_matching = None
    backend = os.path.join(here, "..", "..", "shallowflaws", "app", "core", "matching.py")
    if os.path.exists(backend):
        spec = importlib.util.spec_from_file_location("matching", backend)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        torch_matching = mod.compute_questionnaire_scores
        import torch
        torch.set_num_threads(1)
    bench_matching("matching, 6 applicants x 12 questions", 6, 12, 2000, torch_matching)
    bench_matching("matching, 1000 applicants x 50 questions", 1000, 50, 20, torch_matching)
    bench_matching("matching, 10000 applicants x 50 questions", 10000, 50, 5, torch_matching)
    print("numpy %s, CPython %s" % (np.__version__, sys.version.split()[0]), file=sys.stderr)


main()
