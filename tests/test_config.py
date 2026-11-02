"""SECRET_KEY must not silently fall back to a guessable default on a deployed host."""
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def run_config(extra_env, snippet="print(len(config.settings.SECRET_KEY), config.settings.CORS_ORIGINS)"):
    env = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "RENDER", "ENVIRONMENT", "CORS_ORIGINS")}
    env.update(extra_env)
    # load_dotenv is disabled so a developer's local .env cannot hide the behaviour under test
    code = ("import sys; sys.path.insert(0, %r); import dotenv; dotenv.load_dotenv = lambda *a, **k: None; "
            "import config; %s" % (str(SRC), snippet))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)


def test_missing_key_on_render_refuses_to_start():
    r = run_config({"RENDER": "true"})
    assert r.returncode != 0 and "SECRET_KEY is not set" in r.stderr


def test_old_default_key_on_render_refuses_to_start():
    r = run_config({"RENDER": "true", "SECRET_KEY": "insecure-default-change-me"})
    assert r.returncode != 0 and "SECRET_KEY is not set" in r.stderr


def test_production_environment_also_refuses():
    r = run_config({"ENVIRONMENT": "production"})
    assert r.returncode != 0


def test_real_key_on_render_starts():
    r = run_config({"RENDER": "true", "SECRET_KEY": "x" * 40})
    assert r.returncode == 0 and r.stdout.split()[0] == "40"


def test_local_run_without_key_gets_a_random_one():
    r = run_config({})
    assert r.returncode == 0
    assert int(r.stdout.strip().splitlines()[-1].split()[0]) >= 32   # last line = the printed key length


def test_cors_origins_parsed_and_empty_by_default():
    assert run_config({}).stdout.strip().endswith("[]")
    out = run_config({"CORS_ORIGINS": "https://a.example, https://b.example"}).stdout
    assert "https://a.example" in out and "https://b.example" in out


def test_app_has_no_cors_middleware_by_default(monkeypatch):
    monkeypatch.chdir(SRC)   # the app mounts ./static relative to src/
    import app as app_module
    from fastapi.middleware.cors import CORSMiddleware
    assert not any(m.cls is CORSMiddleware for m in app_module.app.user_middleware)
