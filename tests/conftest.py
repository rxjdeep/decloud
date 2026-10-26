"""Shared fixtures and environment isolation for the reclmr test suite.

Environment variables MUST be set before `app` is imported: shared.py
reads config at import time. Do not import app (or any routes module)
before these lines run.
"""
import os
import pathlib
import tempfile

# ─── Isolate filesystem access + set a test passcode ──────────────
_TMP = pathlib.Path(tempfile.mkdtemp(prefix='reclmr-test-'))
os.environ.setdefault('RECLMR_PIN', '24681357')           # 8-digit test passcode
os.environ.setdefault('SECRET_KEY', 'reclmr-test-secret-key')
os.environ.setdefault('RECLMR_BOOKS_DIR', str(_TMP / 'books'))
os.environ.setdefault('RECLMR_FILES_DIR', str(_TMP / 'files'))
os.environ['RECLMR_BROWSE_ROOT'] = ''          # tests browse the sandbox, never a machine-wide root from a real .env
os.environ['RECLMR_TOTP_FILE'] = str(_TMP / 'totp.json')
os.environ['RECLMR_AUDIT_FILE'] = str(_TMP / 'audit.log')
os.environ['RECLMR_SESSIONS_FILE'] = str(_TMP / 'sessions.json')
os.environ['RECLMR_NAVIDROME_USER'] = os.environ['RECLMR_NAVIDROME_PASS'] = ''   # keep the suite off the real Navidrome (it read/wrote it via the .env credentials)   # else the suite overwrites the real logged-in sessions
os.environ.setdefault('RECLMR_MUSIC_DIR', str(_TMP / 'music'))
os.environ.setdefault('RECLMR_PIPER_DIR', str(_TMP / 'piper'))
os.environ.setdefault('RECLMR_HOST', '127.0.0.1')
os.environ.setdefault('RECLMR_MUSICGEN_DIR', str(_TMP / 'musicgen'))
for _d in ('books', 'files', 'music', 'piper', 'musicgen'):
    (_TMP / _d).mkdir(parents=True, exist_ok=True)

TEST_PASSCODE = os.environ['RECLMR_PIN']

import pytest  # noqa: E402

from app import app  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_limiter():
    """flask-limiter keys by IP (127.0.0.1 for the test client) — reset
    the counter after every test so login rate limits don't leak."""
    from shared import limiter
    yield
    limiter.reset()


@pytest.fixture
def login(client):
    """Log in with the test passcode; returns the JSON payload
    (contains session + csrf tokens)."""
    resp = client.post('/api/auth/login', json={'pin': TEST_PASSCODE})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


@pytest.fixture
def auth_headers(login):
    """Bearer + CSRF headers for an authenticated session."""
    return {
        'Authorization': f"Bearer {login['session']}",
        'X-CSRF-Token': login['csrf'],
    }


@pytest.fixture
def files_dir():
    return pathlib.Path(os.environ['RECLMR_FILES_DIR'])
