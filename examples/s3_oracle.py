"""Capture what boto3 puts on the wire, as the oracle for the `s3` port.

Runs the backend's own boto3/botocore with a frozen clock and fixed
credentials, against no network at all: a `before-send` hook records
every prepared request (method, URL, headers, body) and answers with a
canned response. Presigned URLs need no hook. The result is
src/s3/fixture.tuo: for each case, the request bytes the backend would
send, with the SigV4 signature the port must reproduce.

    ../shallowflaws/.venv/bin/python examples/s3_oracle.py

The client is configured exactly as `get_r2_client` in
app/tasks/extraction_tasks.py: signature_version s3v4, path-style
addressing, region "auto".
"""

import datetime
import json
import os
import sys

import boto3
import botocore
import botocore.auth
import botocore.awsrequest
from botocore.config import Config

FROZEN = datetime.datetime(2026, 9, 22, 8, 30, 0, tzinfo=datetime.timezone.utc)
botocore.auth.get_current_datetime = lambda: FROZEN
import botocore.signers  # noqa: E402

botocore.signers.get_current_datetime = lambda: FROZEN
ACCESS = "AKIAIOSFODNN7EXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
BUCKET = "shallowflaws-uploads"

captured = []


def hook(request, **kwargs):
    body = request.body
    if body is None:
        body = b""
    elif hasattr(body, "read"):
        body = body.read()
    if isinstance(body, str):
        body = body.encode()
    captured.append({
        "method": request.method,
        "url": request.url,
        "headers": {k.lower(): (v.decode() if isinstance(v, bytes) else v) for k, v in request.headers.items() if k.lower() not in SKIPPED},
        "body": body.decode("utf-8", "surrogateescape"),
    })
    status = 200
    text = b""
    if request.method == "GET" and "list-type=2" in request.url:
        text = b'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Name>b</Name><Prefix></Prefix><KeyCount>0</KeyCount><MaxKeys>1000</MaxKeys><IsTruncated>false</IsTruncated></ListBucketResult>'
    elif request.method == "POST" and "delete" in request.url:
        text = b'<?xml version="1.0" encoding="UTF-8"?><DeleteResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"></DeleteResult>'
    raw = botocore.awsrequest.AWSResponse(request.url, status, {"content-type": "application/xml", "content-length": str(len(text))}, None)
    raw._content = text
    return raw


def client(endpoint, **extra):
    c = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=ACCESS,
        aws_secret_access_key=SECRET,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, **extra),
        region_name="auto",
    )
    c.meta.events.register("before-send.s3.*", hook)
    return c


cases = []
# Headers that carry no meaning for the port: identification and retry
# bookkeeping, and the `Expect` botocore adds for large bodies.
SKIPPED = ("user-agent", "amz-sdk-invocation-id", "amz-sdk-request", "expect")


def record(name, fn):
    del captured[:]
    fn()
    for k, r in enumerate(captured):
        cases.append(dict(r, name=name if len(captured) == 1 else f"{name}_{k}"))
        print(cases[-1]["name"], r["method"], r["url"])
        for h, v in r["headers"].items():
            if h.lower() not in SKIPPED:
                print("   ", h + ":", v)
        if r["body"]:
            print("    body:", repr(r["body"][:200]))


def presigned(name, c, op, params, expires):
    url = c.generate_presigned_url(op, Params=params, ExpiresIn=expires)
    cases.append({"name": name, "method": "URL", "url": url, "headers": {}, "body": ""})
    print(name, url)


https = client("https://abc123.r2.cloudflarestorage.com")
http = client("http://localhost:9000")

for label, c in (("https", https), ("http", http)):
    record(f"{label}_put_text", lambda: c.put_object(Bucket=BUCKET, Key="extraction/7/result.csv", Body=b"id,name\n1,Ada\n", ContentType="text/csv; charset=utf-8"))
    record(f"{label}_put_empty", lambda: c.put_object(Bucket=BUCKET, Key="extraction/7/empty.txt", Body=b"", ContentType="text/plain"))
    record(f"{label}_put_binary", lambda: c.put_object(Bucket=BUCKET, Key="photos/job_applicant/logo one.png", Body=bytes(range(128)), ContentType="image/png"))
    record(f"{label}_get", lambda: c.get_object(Bucket=BUCKET, Key="photos/job_applicant/logo one.png"))
    record(f"{label}_head", lambda: c.head_object(Bucket=BUCKET, Key="extraction/7/result.csv"))
    record(f"{label}_delete", lambda: c.delete_object(Bucket=BUCKET, Key="extraction/7/result.csv"))
    record(f"{label}_list", lambda: c.list_objects_v2(Bucket=BUCKET, Prefix="extraction/7/docs/"))
    record(f"{label}_list_continued", lambda: c.list_objects_v2(Bucket=BUCKET, Prefix="extraction/7/docs/", ContinuationToken="1/abc+def=", MaxKeys=50))
    record(f"{label}_delete_objects", lambda: c.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": "extraction/7/docs/a.pdf"}, {"Key": "extraction/7/docs/b & c.pdf"}]}))
    presigned(f"{label}_presign_get", c, "get_object", {"Bucket": BUCKET, "Key": "extraction/7/result.csv", "ResponseContentType": "text/csv; charset=utf-8", "ResponseContentDisposition": 'attachment; filename="result.csv"'}, 900)
    presigned(f"{label}_presign_put", c, "put_object", {"Bucket": BUCKET, "Key": "extraction/7/source.zip", "ContentType": "application/zip"}, 3600)

if "--print" in sys.argv:
    sys.exit(0)
json.dump(cases, open(os.path.join(os.path.dirname(__file__), "s3_oracle.json"), "w"), indent=1)
print("wrote", len(cases), "cases")
