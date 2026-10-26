import os
import pytest
from .local_db import LocalDB


@pytest.fixture(scope='session')
def schema_db():
    if os.environ.get('BLIPZ_V2_ISOLATED_DB_TESTS')!='1':
        pytest.skip('Opt-in disposable Docker PostgreSQL tests only; no external DSN accepted')
    db=LocalDB().start()
    try:
        db.migrate('20261026_v2_schema.sql')
        yield db
    finally:db.stop()
