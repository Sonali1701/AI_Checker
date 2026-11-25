"""Account desks share vendor imports, but must never share runtime settings."""
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portal_vendor import data_filler as engine
from portal_vendor import match_paper, rejection_browser


def test_nested_runtime_context_restores_settings_and_hooks_after_failure(monkeypatch):
    monkeypatch.setattr(engine, "CDP_PORT", 9222)
    prompts = []
    with engine.runtime_context(CDP_PORT=9401, should_stop=lambda: True, ask_continue=prompts.append):
        assert engine.runtime_setting("CDP_PORT") == 9401
        assert engine.should_stop()
        engine.ask_continue("admin login")
        with pytest.raises(RuntimeError, match="failed"):
            with engine.runtime_context(CDP_PORT=9402, should_stop=lambda: False):
                assert engine.runtime_setting("CDP_PORT") == 9402
                assert not engine.should_stop()
                engine.ask_continue("evaluator login")
                raise RuntimeError("failed")
        assert engine.runtime_setting("CDP_PORT") == 9401
        assert engine.should_stop()
    assert engine.runtime_setting("CDP_PORT") == 9222
    assert not engine.should_stop()
    assert prompts == ["admin login", "evaluator login"]


def test_parallel_accounts_launch_their_own_profiles_and_keep_stop_hooks(tmp_path, monkeypatch):
    overlap = threading.Barrier(2)
    listening = threading.local()
    launched = []
    prompts = []
    guard = threading.Lock()

    def chrome_is_listening():
        listening.calls = getattr(listening, "calls", 0) + 1
        return listening.calls > 1

    def launch(args):
        with guard:
            launched.append(args)
        return object()

    monkeypatch.setattr(engine, "chrome_is_listening", chrome_is_listening)
    monkeypatch.setattr(engine, "find_chrome", lambda: Path("chrome.exe"))
    monkeypatch.setattr(engine.subprocess, "Popen", launch)
    baseline = (engine.CDP_PORT, engine.USER_DATA_DIR, engine.CONFIRM_FIRST_N)

    def account(number):
        profile = tmp_path / f"account-{number}"
        def prompt(message):
            with guard:
                prompts.append((number, message))
        with engine.runtime_context(CDP_PORT=9400 + number, USER_DATA_DIR=profile,
                                    CONFIRM_FIRST_N=number, should_stop=lambda: number == 1,
                                    ask_continue=prompt):
            overlap.wait(timeout=5)
            engine.start_chrome()
            # Sibling modules' imports resolve to the canonical engine. Their
            # hooks must see this account's scope, rather than another desk's.
            assert match_paper.engine.should_stop() is (number == 1)
            assert rejection_browser.engine.should_stop() is (number == 1)
            assert engine.runtime_setting("CONFIRM_FIRST_N") == number
            engine.ask_continue("login")
            overlap.wait(timeout=5)
        return engine.runtime_setting("CDP_PORT")

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(account, (1, 2))) == [baseline[0], baseline[0]]
    assert (engine.CDP_PORT, engine.USER_DATA_DIR, engine.CONFIRM_FIRST_N) == baseline
    assert sorted(prompts) == [(1, "login"), (2, "login")]
    for number in (1, 2):
        args = next(args for args in launched if f"--remote-debugging-port={9400 + number}" in args)
        assert f"--user-data-dir={(tmp_path / f'account-{number}').resolve()}" in args


def test_listening_probe_and_screenshot_use_current_account(tmp_path, monkeypatch):
    requested = []

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(engine.urllib.request, "urlopen", lambda url, **kwargs: (requested.append(url), Response())[1])
    screenshots = []
    with engine.runtime_context(CDP_PORT=9499, SCREENSHOT_DIR=tmp_path / "admin-shots"):
        assert engine.chrome_is_listening()
        screenshot = engine.save_screenshot(SimpleNamespace(screenshot=lambda **kwargs: screenshots.append(kwargs["path"])), "student/1")
    assert requested == ["http://127.0.0.1:9499/json/version"]
    assert screenshots == [screenshot]
    assert Path(screenshot).parent == tmp_path / "admin-shots"


def test_standalone_login_prompt_keeps_terminal_default(monkeypatch):
    prompts = []
    monkeypatch.setattr("builtins.input", prompts.append)
    engine.ask_continue("Log in")
    assert prompts == [">>> Log in Press Enter to continue..."]
