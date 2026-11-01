# Mainline Agent report

Status: **completed**

Branch: `main` · Base: `7db79b32d52dfdd31fca55b11745c87e333a25ca` · Head: `472765fc9f90a9d7cd4afed92c26afe752567a5e`

Mode: `local_model_and_static` · Model: `gemma4:latest`

Analysis covers selected changes and retrieved evidence. It does not certify the whole repository.

## Findings

### documentation_drift (verified_static)

fetch example no longer binds to its declared signature: got an unexpected keyword argument 'retries'

- README.md:5 @ 472765fc9f90a9d7cd4afed92c26afe752567a5e — fetch('https://example.org', retries=3)
- client.py:1 @ 472765fc9f90a9d7cd4afed92c26afe752567a5e — def fetch(url, timeout=30):
- client.py:1 @ 7db79b32d52dfdd31fca55b11745c87e333a25ca — def fetch(url, retries=3):

### documentation_drift (model_suggested)

The signature of the `fetch` function in `client.py` has changed from accepting `retries` to accepting `timeout`, but the documentation in `README.md` still shows usage with `retries=3`.

- client.py:1 @ 472765fc9f90a9d7cd4afed92c26afe752567a5e — def fetch(url, timeout=30):
- README.md:5 @ 472765fc9f90a9d7cd4afed92c26afe752567a5e — fetch('https://example.org', retries=3)

## Coverage

```json
{
  "documents_read": 1,
  "code_files_read": 1,
  "model_packets": 1,
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
