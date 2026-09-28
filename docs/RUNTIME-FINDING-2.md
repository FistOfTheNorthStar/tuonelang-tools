# Finding: there is no way to read the process environment

**Status:** open. The `settings` port works around it by taking the
environment as an argument.
**Found by:** porting pydantic-settings, whose first source is `os.environ`.
**Severity:** a program cannot read its own configuration the way every
deployment of the backend supplies it (docker compose `environment:`,
systemd `Environment=`, the Celery worker's `CELERY_QUEUES`). A `.env`
file works because `std::fs::read` exists; the environment does not.

## What is there

The runtime's effect primitives (`crates/tuo-resolve/src/builtin.rs`) cover
argv (`arg_count`, `arg_byte`), files (`open`, `read_byte`, `write`,
`remove_file`), sockets, channels, mutexes, the monotonic clock, random
bytes, and `exit`. There is nothing for the environment. There's no file
fallback either: on macOS, which this repo is developed on, there is no
`/proc/self/environ`.

This was deliberate. ADR-0013 (`specification/adr/ADR-0013-os-effect-boundary.md`)
lists environment variables under *Deliberately out of scope*, "additive
on this seam when its need is demonstrated by dogfooding". This is that
demonstration: the backend's `Settings` reads 93 fields from the
environment first and `.env` second, and the environment wins.

## What the port does instead

`settings::load::load` and `load_path` take a `dotenv::parse::Environ`
(names and values) from the caller, as `tls` takes its time from the
caller. `dotenv::parse::environ_from_lines` builds one from `NAME=value`
lines, the shape `env` prints, so a launcher can run
`env > /tmp/env && prog`, or pass pairs on argv. Everything above the
environment is spec'd and matches pydantic-settings for all 33 captured
cases. Only the read itself is missing.

## What would close it

Two primitives with the same shape as argv's, which ADR-0013 already
established:

```
std::rt::env_count() -> Int                     // entries in environ
std::rt::env_byte(take i: Int, take j: Int) -> Int  // byte j of "NAME=value" i, -1 past the end
```

With those, `std::process` (or a new `std::env`) can offer
`var(name) -> Option[String]` and `vars() -> Array[String]` in tuonelang,
the same way `std::process::arg` is written over `arg_byte`. After that,
`settings::load` needs a one-line `from_process` that builds its
`Environ` from `vars()`. Nothing else in the port changes. A setter is
not needed: pydantic-settings only reads, and `load_dotenv`'s writing
side is not something the backend uses.
