# Compiler finding: a `Str` borrowed from an array element and returned dangles natively

Found 2026-09-22 while writing the S3 live oracle. `tuo check` accepts the
program, `tuo verify` passes its specs, and the native build — Cranelift
and LLVM alike — reads freed memory.

## Reproduction

```tuo
module m2;

fn key_at(in keys: Array[String], take i: Int) -> Str {
    std::string::as_str(std::array::get(keys, i))
}

fn sample() -> Array[String] {
    var keys = std::array::empty();
    std::array::push(keys, std::string::from_str("hello"));
    keys
}

spec key_at {
    then key_at(sample(), 0) == "hello";
}

fn main() -> Int {
    let keys = sample();
    let _ = std::rt::write(1, key_at(keys, 0));
    let _ = std::rt::write(1, "|\n");
    0
}
```

```
$ tuo verify m2.tuo
1 passed, 0 failed of 1 spec
$ tuo run m2.tuo
>��9B|
$ tuo build --release m2.tuo -o m2 && ./m2
��@_s|
```

The bytes differ from run to run: the `Str` points at memory that was
freed when `key_at` returned.

## Analysis

`std::array::get(keys, i)` on an `Array[String]` yields a `String` value
— a temporary owned by `key_at`'s frame — and `std::string::as_str`
borrows it. Returning that borrow lets it outlive the temporary. The
ownership checker should refuse the return (the borrow's origin is a
local that is dropped at the end of the function), or `std::array::get`
on an array of owned values should be defined as yielding a borrow tied
to `keys`, which is what `in keys` would make sound. The interpreter
happens to keep the temporary alive, which is why `verify` cannot catch
this: it is the one class of bug the differential suite does not see.

The same expression *inside* a function is fine, and so is returning an
owned `String` (`std::string::from_str(std::string::as_str(...))`).

## Workaround

Never return a `Str` obtained from `std::array::get`. Return a `String`
copy, or compare inside the function and return the `Bool`.

## Status

Not yet reported upstream as of 2026-09-22. This crate's `examples/s3.tuo`
returns copies.
