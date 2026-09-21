# Compiler finding: a local named like a module's first segment silently drops the function

Found 2026-09-15 while adding `crypto::sha384` (then called `hash::sha384`)
to the TLS client. `tuo check` accepts the program; `tuo verify` traps
every spec that calls the function, and `tuo build` refuses the program,
both without pointing at the cause.

## Reproduction

Two modules, both passed to the compiler:

```tuo
module hash::sha384;

pub fn hash(in message: Array[Int]) -> Int {
    std::array::len(message)
}
```

```tuo
module probe;

fn length(in hash: Array[Int]) -> Int {
    hash::sha384::hash(hash)
}

spec length {
    then length(std::array::empty()) == 0;
}
```

`tuo check` passes. `tuo verify` reports:

```
then trapped: length(std::array::empty()) == 0
  trap internal: the interpreter only executes functions present in the lowered MIR
```

and `tuo build` of a `main` that calls `length`:

```
error: cannot build (codegen): call to a function outside the lowered program (v0 has no external calls)
```

Renaming the parameter (`in transcript: Array[Int]`) or the module
(`crypto::sha384`) makes both pass.

## What seems to happen

The path `hash::sha384::hash` is resolved with a local variable `hash` in
scope. The type checker resolves the path to the module function and
accepts the call; MIR lowering apparently resolves the first segment to
the local, cannot lower the call, and drops the enclosing function from
the lowered program instead of reporting an error. Every caller then hits
"function not present in the lowered MIR" at run time, which is a
diagnostic about the *caller*, so the search starts in the wrong place.

## What this crate does about it

The module is `crypto::sha384`, and `tls::handshake::signature_valid`'s
transcript parameter is not named after any module. The bisection that
found this is the four-function probe in this finding's history:
functions using a temporary struct argument, a temporary array argument,
and both, all lowered fine; only the one whose parameter shadowed a
module's first segment did not.

## Suggested fix upstream

Either make name resolution consistent between the checker and lowering
(a path with two or more `::` segments can never begin with a local), or
make lowering report the function it dropped, with the position of the
call it could not lower. The silent drop is the expensive part.
