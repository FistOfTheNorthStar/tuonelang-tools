# Test certificates

Everything here is a **test key**: it authenticates nothing outside this
repository and must never be used for anything else. `run-tests.sh --live`
starts `openssl s_server` instances with these so the TLS client can be
checked against an independent implementation.

## `cert.pem`, `key.pem` — the pinned Ed25519 certificate

A self-signed Ed25519 certificate for `CN=localhost`, generated on
2026-09-15 with

```bash
openssl req -x509 -newkey ed25519 -keyout key.pem -out cert.pem -subj "/CN=localhost" -days 36500 -nodes
```

Served on port 4433; the client pins its public key. The same bytes are
in `src/tls/fixture.tuo` for the loopback oracle.

## `ec-*.pem`, `big-*.pem` — two chains for validation

Two three-level PKIs generated on 2026-09-15 with OpenSSL 3.6 and the
extensions in `ext.cnf` (roots and intermediates with `CA:TRUE` and
`keyCertSign`, the intermediate with `pathlen:0`; leaves with
`digitalSignature`, `serverAuth`, and SANs `localhost`, `example.test`,
`*.wild.test`). Roots and intermediates are valid 2026-01-01 to
2046-01-01, leaves 2026-09-01 to 2046-01-01.

| prefix | root | intermediate | leaf | signatures |
|---|---|---|---|---|
| `ec` | P-384 | P-256 | P-256 | ecdsa-with-SHA384 |
| `big` | RSA-2048 | RSA-2048 | RSA-2048 | sha256WithRSAEncryption |

`ec-leaf` is served on 4434 and `big-leaf` on 4435, each with its
intermediate as the chain; the client validates against the matching
root. Only the leaf keys are kept; the CA keys were discarded after
signing. The DER of all six certificates, plus an RSA-1024 PKI made the
same way for the spec sandbox, is in `src/x509/fixture.tuo`.

To regenerate (from this directory, for one prefix):

```bash
openssl ecparam -name secp384r1 -genkey -noout -out ec-root.key
openssl ecparam -name prime256v1 -genkey -noout -out ec-int.key
openssl ecparam -name prime256v1 -genkey -noout -out ec-leaf.key
openssl req -x509 -new -key ec-root.key -subj "/O=tuo test/CN=ec root" -sha384 \
  -config ext.cnf -extensions root -out ec-root.pem \
  -not_before 20260101000000Z -not_after 20460101000000Z
openssl req -new -key ec-int.key -subj "/O=tuo test/CN=ec intermediate" -out ec-int.csr
openssl x509 -req -in ec-int.csr -CA ec-root.pem -CAkey ec-root.key -set_serial 2 -sha384 \
  -extfile ext.cnf -extensions ca -out ec-int.pem \
  -not_before 20260101000000Z -not_after 20460101000000Z
openssl req -new -key ec-leaf.key -subj "/CN=localhost" -out ec-leaf.csr
openssl x509 -req -in ec-leaf.csr -CA ec-int.pem -CAkey ec-int.key -set_serial 3 -sha384 \
  -extfile ext.cnf -extensions leaf -out ec-leaf.pem \
  -not_before 20260901000000Z -not_after 20460101000000Z
```

then paste the DER (`openssl x509 -outform DER | xxd -p`) into
`src/x509/fixture.tuo`.
