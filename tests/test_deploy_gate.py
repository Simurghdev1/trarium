"""Public-deploy safety: the optional password gate hides the whole UI.

Runs the app in a separate process so Streamlit's module state does not leak
into the other tests.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
import sys
from streamlit.testing.v1 import AppTest
at = AppTest.from_file(sys.argv[1], default_timeout=90).run()
assert not at.exception, at.exception
if sys.argv[2] == "gate":
    assert len(at.tabs) == 0
    at.text_input(key="_trarium_password").input("wrong").run()
    assert len(at.tabs) == 0 and at.error
    at.text_input(key="_trarium_password").input("s3cret-pass").run()
assert len(at.tabs) == 13, len(at.tabs)
print("OK")
'''


def _run(tmp_path, mode, password):
    env = {**os.environ, "TRARIUM_HOME": str(tmp_path)}
    env.pop("TRARIUM_APP_PASSWORD", None)
    if password:
        env["TRARIUM_APP_PASSWORD"] = password
    done = subprocess.run([sys.executable, "-c", SCRIPT, str(ROOT / "streamlit_app.py"), mode],
                          env=env, cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and "OK" in done.stdout, done.stderr[-2000:]


def test_password_gate_blocks_ui(tmp_path):
    _run(tmp_path, "gate", "s3cret-pass")


def test_no_gate_without_password(tmp_path):
    _run(tmp_path, "open", "")
