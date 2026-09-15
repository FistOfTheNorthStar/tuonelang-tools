# A test certificate

`cert.pem` and `key.pem` are a self-signed Ed25519 certificate for
`CN=localhost` and its private key, generated on 2026-09-15 with

```bash
openssl req -x509 -newkey ed25519 -keyout key.pem -out cert.pem -subj "/CN=localhost" -days 36500 -nodes
```

They exist so `run-tests.sh --live` can start `openssl s_server` with a key
this crate's client knows the public half of. The same bytes are embedded in
`src/tls/fixture.tuo` for the loopback oracle. **The key is a test key**: it
authenticates nothing outside this repository and must never be used for
anything else.
