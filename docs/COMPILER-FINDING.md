# Finding: a temporary in the right operand of `&&` / `||` fails MIR verification

**Status:** resolved upstream — tuonelang commit `3a06c2b` (PR #61, merged
2026-09-15) found and fixed the same bug independently: `short_circuit`
lowered its conditional operand with a bare `self.expr()` instead of the
`scoped_value` snapshot/restore every other branching construct uses. The
reproduction below builds with a `tuo` from `f424add`, and the workaround
in `examples/http.tuo` is now only a comment's worth of history.
**Found by:** writing `examples/http.tuo`, whose client checks read a
response header inside a `&&` chain.
**Severity:** any program with the shape below is refused by `tuo build` /
`tuo run` with an internal error and no location. `tuo check` accepts it.
Nothing is mis-compiled — the compiler refuses, as it is designed to — but
the refusal is unlocated on the native path and the shape is an ordinary
one.

## What happens

```tuo
fn make() -> String {
    std::string::from_str("x")
}

fn is_x(in s: String) -> Bool {
    std::string::as_str(s) == "x"
}

pub fn probe(take flag: Bool) -> Int {
    if flag && is_x(make()) {
        return 0;
    }
    1
}
```

```
$ tuo check s4.tuo        # accepted
$ tuo build s4.tuo
error: codegen: internal: lowered MIR failed verification
$ tuo spec  s4.tuo        # the same program with a spec on `probe`
error[M0005]: fn `probe`: bb4 reads _2 after it was moved out
error[M0005]: fn `probe`: bb6 reads _2 after it was moved out
```

The temporary `String` that `make()` returns is `_2`. Lowered under the
short-circuit, it is moved out (into the `in` argument, or its drop) on one
path and read again on another. The interpreter path (`tuo spec`) names the
function and the blocks; the native path (`tuo build`) reports only the
internal failure.

## What does and does not trigger it

| Shape | `tuo build` |
|---|---|
| `flag && is_x(make())` — temporary on the right of `&&` | **refused** |
| `flag \|\| is_x(make())` — temporary on the right of `\|\|` | **refused** |
| `is_x(make()) && flag` — temporary on the left | built |
| `let made = make(); flag && is_x(made)` — bound first | built |
| `is_x(make())` alone, not under a short-circuit | built |

Any owned temporary does it — a `String` here; a struct with array fields
in the original (`text_is(http::client::header(r8, "Location"), "/")` as the
third operand of a `&&` chain).

Reduced against `tuo 0.1.0` built from tuonelang `main` at `81be726` on
2026-09-15.

## Consequence for this crate

`examples/http.tuo` binds the header before the condition and says why in a
comment. No other source in this crate has the shape — every other `&&`
chain compares scalars or borrows fields — and `run-tests.sh` builds every
example natively, so a regression would show as a failed build rather than
a wrong answer.
