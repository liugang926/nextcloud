# WeKnora Nextcloud API contract

[`weknora-nextcloud-openapi.json`](weknora-nextcloud-openapi.json) documents the 27
Nextcloud-specific WeKnora HTTP method/path pairs present in both pinned patch
baselines. It covers the signed event receiver, event and exact-file status,
interactive ask-target resolution, administrator event connections, source
pairing and rotation, failed candidates and exact retry, withdrawal, and GC
status. The two services have separate specifications: `openapi.yaml` describes
the Nextcloud app's endpoints.

After applying **one** pinned WeKnora patch to its clean baseline, run:

```sh
python3 scripts/ops/check-weknora-openapi-contract.py /path/to/patched-weknora
```

The standard-library checker compares the specification with the registered Go
methods and paths. It checks required query and signed-request headers, the
primary success response's required JSON keys, and 11 request/response structs'
JSON field tags. CI runs it independently after applying each pinned patch. A
route method change, omitted failed-candidate cursor, or changed file-status
ETag field fails the check. The contract check does not replace the Go handler,
database, HMAC, or live Docker tests; it does not certify enterprise AD or
production TLS behavior.
