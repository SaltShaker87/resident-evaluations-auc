"""Test fixtures.

The single most important thing here is the first few lines: AUC_DATA_DIR is
pointed at a scratch directory *before* anything imports the application.
Importing app.py creates the data directory and opens the database, so without
this a test run would touch real resident data. test_never_touches_real_data
asserts it worked.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
AUC_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

# Must happen before the first `import config` anywhere in the process.
SCRATCH_DIR = Path(tempfile.mkdtemp(prefix="auc-tests-"))
os.environ["AUC_DATA_DIR"] = str(SCRATCH_DIR)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TEST_PASSWORD = "correct-horse-battery-staple"


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(SCRATCH_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def app_module():
    import app

    return app


@pytest.fixture
def client(app_module):
    """A client against a fresh, empty database for every test.

    The database is deleted and the app's startup hook re-run each time, so no
    test can depend on another having run first.
    """
    for leftover in SCRATCH_DIR.glob("auc.db*"):
        leftover.unlink()
    for photo in (SCRATCH_DIR / "photos").glob("*"):
        photo.unlink()

    # TestClient as a context manager fires the startup event, which is what
    # creates the schema.
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture
def logged_in(client):
    """A client that has completed first-run setup and holds a session cookie."""
    response = client.post("/api/auth/setup", json={"password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    return client
