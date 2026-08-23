import os, tempfile
os.environ.setdefault("TW_DATA_DIR", tempfile.mkdtemp(prefix="tw-test-"))
