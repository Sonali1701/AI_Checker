"""PWSkillsChecker.exe: the same server in its PW Skills mode (14 Nov 2026, for the PW Skills team).

The server reads the mode when it is imported, so each case runs in a fresh process.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE = textwrap.dedent("""
    import asyncio, json, os, sys
    sys.path.insert(0, os.getcwd())
    if os.environ.get("ENTRY") == "skills":
        import run_skills                      # sets the mode, start page and data folder
    import httpx, run_client, server
    async def main():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
            pages = {}
            for path in ("/", "/index.html", "/workflow.html", "/skills.html"):
                answer = await http.get(path)
                pages[path] = [answer.status_code, answer.headers.get("location", "")]
            config = (await http.get("/api/config")).json()
        base = server.BUILD_ID[:-len("-skills")] if server.BUILD_ID.endswith("-skills") else server.BUILD_ID
        print(json.dumps({"mode": server.APP_MODE, "build": server.BUILD_ID, "base": base, "pages": pages,
                          "config_mode": config.get("app_mode"), "config_build": config.get("build_id"),
                          "data_dir": os.environ.get("AICHECKER_DATA_DIR", ""),
                          "workflow_root": str(server._WORKFLOW_STORE.root),
                          "default_root": server._default_workflow_dir().name,
                          "start_url": run_client._browser_url("http://127.0.0.1:8765"),
                          "sibling_of_other_mode": run_client._sibling(base if server.APP_MODE == "skills" else base + "-skills"),
                          "sibling_of_itself": run_client._sibling(server.BUILD_ID),
                          "sibling_of_older": run_client._sibling("2026.01.01.100-old"),
                          "sibling_of_nothing": run_client._sibling("")}))
    asyncio.run(main())
""")


def probe(tmp_path, entry):
    # Every folder the server writes is under tmp_path; the default it would use next to the
    # app is read from server._default_workflow_dir() without being created.
    env = {**os.environ, "ENTRY": entry, "AI_CHECKER_WORKFLOW_DIR": str(tmp_path / "runs"), "AICHECKER_START_PAGE": "",
           "AICHECKER_APP_MODE": "", "AICHECKER_DATA_DIR": "" if entry == "skills" else str(tmp_path / "data"),
           "LOCALAPPDATA": str(tmp_path / "local")}
    done = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads([line for line in done.stdout.splitlines() if line.startswith("{")][-1])


@pytest.fixture(scope="module")
def skills(tmp_path_factory):
    return probe(tmp_path_factory.mktemp("skills"), "skills")


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    return probe(tmp_path_factory.mktemp("full"), "full")


def test_the_skills_tool_reports_its_own_build_and_mode(skills, full):
    assert skills["mode"] == "skills" and skills["build"] == full["build"] + "-skills"
    assert skills["config_mode"] == "skills" and skills["config_build"] == skills["build"]
    assert full["mode"] == "full" and full["config_mode"] == "full"


def test_the_skills_tool_shows_only_the_skills_page(skills, full):
    for path in ("/", "/index.html", "/workflow.html"):
        assert skills["pages"][path] == [307, "/skills.html"], path
        assert full["pages"][path][0] == 200, path
    assert skills["pages"]["/skills.html"][0] == 200


def test_the_skills_tool_opens_the_skills_page_and_keeps_its_own_data(skills):
    assert skills["start_url"] == "http://127.0.0.1:8765/skills.html"
    assert skills["data_dir"].endswith("AI Copy Checker - PW Skills")
    assert skills["default_root"] == "pw_skills_data"


def test_the_full_app_keeps_its_workflow_data_folder(full):
    assert full["default_root"] == "workflow_data"


def test_each_launcher_passes_over_the_other_modes_server_only(skills, full):
    for side in (skills, full):
        assert side["sibling_of_other_mode"] is True
        assert side["sibling_of_itself"] is False
        assert side["sibling_of_older"] is False
        assert side["sibling_of_nothing"] is False
