from pathlib import Path
import shutil

import pytest


@pytest.fixture()
def fixture_repo(tmp_path):
    src = Path(__file__).parent / "fixtures" / "tiny_repo"
    dst = tmp_path / "tiny_repo"
    shutil.copytree(src, dst)
    return dst
