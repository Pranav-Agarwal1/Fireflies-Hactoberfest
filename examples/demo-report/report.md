# Mainline Agent report

Status: **completed**

Branch: `main` · Base: `7db79b32d52dfdd31fca55b11745c87e333a25ca` · Head: `472765fc9f90a9d7cd4afed92c26afe752567a5e`

Mode: `static_only` · Model: `disabled`

Analysis covers selected changes and retrieved evidence. It does not certify the whole repository.

## Findings

### documentation_drift (verified_static)

fetch example no longer binds to its declared signature: got an unexpected keyword argument 'retries'

- README.md:5 @ 472765fc9f90a9d7cd4afed92c26afe752567a5e — fetch('https://example.org', retries=3)
- client.py:1 @ 472765fc9f90a9d7cd4afed92c26afe752567a5e — def fetch(url, timeout=30):
- client.py:1 @ 7db79b32d52dfdd31fca55b11745c87e333a25ca — def fetch(url, retries=3):

Responsible change in client.py

    diff --git a/client.py b/client.py
    index f2a0cbf..ffb8c50 100644
    --- a/client.py
    +++ b/client.py
    @@ -1,2 +1,2 @@
    -def fetch(url, retries=3):
    +def fetch(url, timeout=30):
         return url

## Coverage

```json
{
  "documents_read": 1,
  "code_files_read": 1,
  "model_packets": 0,
  "skipped": [],
  "limits": {
    "files": 30,
    "docs": 200,
    "packets": 40
  },
  "scope": "Changed files, retrieved documentation, and supplied discussions only; not whole-repo proof."
}
```

## Warnings

None.

## Errors

None.
