import os, tempfile
os.environ.setdefault("SNITCH_DATA_DIR", tempfile.mkdtemp(prefix="tw-test-"))
