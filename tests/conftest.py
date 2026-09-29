import os
import tempfile
from pathlib import Path

import pytest

# Keep tests away from the real database and Drive folder. Must run before
# `app.config` is imported anywhere.
_tmp = Path(tempfile.mkdtemp(prefix="transcribeme-tests-"))
os.environ["DATA_DIR"] = str(_tmp / "data")
os.environ["DRIVE_DIR"] = str(_tmp / "no-such-parent" / "drive")

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def speech_wav() -> Path:
    return FIXTURES / "hello.wav"
