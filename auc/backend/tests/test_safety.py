"""The test suite must never be able to reach real data."""

from pathlib import Path

from conftest import SCRATCH_DIR


def test_never_touches_real_data(app_module):
    """Every path the app writes to has to be inside the scratch directory.

    If this fails, stop and fix it before running anything else: a test run
    would be writing to the live database.
    """
    import backup
    import summary_builder

    real = Path(__file__).resolve().parents[2] / "data"

    for label, path in [
        ("app.DB_PATH", app_module.DB_PATH),
        ("app.PHOTOS_DIR", app_module.PHOTOS_DIR),
        ("backup.DB_PATH", backup.DB_PATH),
        ("backup.PHOTOS_DIR", backup.PHOTOS_DIR),
        ("summary_builder.VALIDATION_LOG", summary_builder.VALIDATION_LOG),
    ]:
        assert SCRATCH_DIR in Path(path).parents, f"{label} escaped the scratch directory: {path}"
        assert real not in Path(path).parents, f"{label} points at real data: {path}"
