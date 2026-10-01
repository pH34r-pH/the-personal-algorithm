# Test boundary

`tests/` is the executable contract for the package. Keep tests local and hermetic:

- `test_application.py`, `test_instance_auth.py`, and `test_*api.py` cover public/private
  route boundaries and owner authorization.
- `test_store.py`, `test_archives.py`, and `test_bootstrap.py` cover SQLite persistence,
  content-addressed raw archives, and resumable provider imports.
- Provider and source tests use `tests/fixtures/*.xml`; they must not fetch live feeds,
  call Azure, or contain real personal data.
- Add a focused regression with any behavior change. Do not delete a test to make a
  documentation or source change pass.

Run from the repository root:

```bash
python -m pip install -e ".[dev]"
pytest -q
```
