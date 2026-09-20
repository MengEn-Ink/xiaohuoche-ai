from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest

from xzq.models import ArticleState, Screen
from xzq.renderer.to_longimage import article_content_hash

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "aime-app" / "xiaohuoche-ribao"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def app_module():
    return load_module("xzq_aime_app_server", APP_ROOT / "app/server/main.py")


@pytest.fixture
def workbench(app_module, tmp_path):
    # Unit tests fake the renderer, so export capability must not depend on the
    # test machine having Playwright browsers installed.
    return app_module.Workbench(tmp_path, export_available=True)


def draft_issue(workbench, issue_id="0913-ribao"):
    issue = workbench.create_issue({"date": "0913", "kind": "ribao", "id": issue_id})
    article = workbench.article(issue["id"])
    article.state = ArticleState.DRAFT
    article.title = "0913，小火车日报"
    article.screens = [Screen("cover", ["真实正文"]), Screen("end", ["收车"])]
    article.review_items = list(workbench.checklist)
    article.review_checked = [False] * len(workbench.checklist)
    workbench.queue.save(article)
    return issue["id"]


def test_app_manifest_uses_real_service_command_and_direct_command_schema():
    manifest = json.loads((APP_ROOT / "app.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "xiaohuoche-ribao"
    service = manifest["services"][0]
    assert service["type"] == "command" and service["auto_start"] is True
    assert service["command"] == "bash app/server/start.sh"
    assert service["health_check"]["path"] == "/healthz"
    command = manifest["commands"][0]
    assert command == {**command, "name": "ribao", "mode": "direct", "type": "script", "script": "commands/ribao.py"}
    assert "ui_provider" not in manifest  # 工作台只能由 /ribao capability 命令打开


def test_managed_data_directory_precedence(app_module, monkeypatch, tmp_path):
    app_data = tmp_path / "app"
    plugin_data = tmp_path / "plugin"
    monkeypatch.setenv("AIME_APP_DATA_DIR", str(app_data))
    monkeypatch.setenv("AIME_PLUGIN_DATA_DIR", str(plugin_data))
    assert app_module.data_root() == app_data.resolve()
    monkeypatch.delenv("AIME_APP_DATA_DIR")
    assert app_module.data_root() == plugin_data.resolve()


def test_issue_material_dedup_and_material_change_invalidates_approval(workbench, tmp_path):
    issue_id = draft_issue(workbench)
    store = workbench.store(issue_id)
    article = workbench.article(issue_id)
    article.review_checked = [True] * len(workbench.checklist)
    article.state = ArticleState.IMAGE
    old = store.exports_dir / f"{issue_id}.png"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    article.image_path = str(old)
    article.cover_path = str(store.exports_dir / f"{issue_id}-cover.jpg")
    article.content_hash = article.export_hash = "old-hash"
    workbench.queue.save(article)

    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(image, format="PNG")
    request = {"name": "ride.png", "bytes": image.getvalue(), "kind": "photo", "text": "骑行合影"}
    payload, duplicate = workbench.add_material(issue_id, request)
    assert not duplicate
    assert payload["state"] == "inbox"
    changed = workbench.article(issue_id)
    assert changed.screens == []
    assert changed.review_checked == []
    assert changed.image_path == changed.cover_path == changed.export_hash == ""

    payload, duplicate = workbench.add_material(issue_id, request)
    assert duplicate
    assert len(payload["materials"]) == 1


def test_generate_uses_engine_and_offline_draft_is_gated(workbench):
    issue = workbench.create_issue({"date": "0914", "kind": "ribao"})
    generated = workbench.generate(issue["id"], {"offline": True})
    assert generated["state"] == "draft"
    assert generated["screens"]
    assert any("离线占位内容" in reason for reason in generated["status"]["gate"]["reasons"])


def test_review_cas_approval_and_revision_invalidation(workbench):
    issue_id = draft_issue(workbench)
    store = workbench.store(issue_id)
    base = store.load()
    base["generalNote"] = "第一位审阅者"
    saved = workbench.save_review(issue_id, base)
    with pytest.raises(RuntimeError):
        workbench.save_review(issue_id, base)

    status = workbench.checklist_update(issue_id, {"checked": [True] * len(workbench.checklist)})
    approved = workbench.approve(issue_id, {"revision": saved["revision"]})
    assert status["gate"]["passed"] is True
    assert approved["status"]["articleState"] == "approved"

    revised = workbench.revise(issue_id, {
        "revision": approved["review"]["revision"],
        "changes": {"screens": {"0": {"text": ["修订正文"]}}},
    })
    changed = workbench.article(issue_id)
    assert revised["revisionId"]
    assert changed.state == ArticleState.DRAFT
    assert changed.review_checked == []
    assert changed.image_path == changed.export_hash == ""


def test_concurrent_review_cas_allows_only_one_writer(workbench):
    issue_id = draft_issue(workbench)
    base = workbench.store(issue_id).load()
    barrier = threading.Barrier(3)
    results = []

    def save(note):
        payload = json.loads(json.dumps(base))
        payload["generalNote"] = note
        barrier.wait()
        try:
            workbench.save_review(issue_id, payload)
            results.append("saved")
        except RuntimeError:
            results.append("conflict")

    threads = [threading.Thread(target=save, args=(str(index),)) for index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["conflict", "saved"]


def test_export_two_phase_cas_creates_full_cover_and_slices(app_module, workbench, monkeypatch):
    from PIL import Image

    issue_id = draft_issue(workbench)
    store = workbench.store(issue_id)
    workbench.checklist_update(issue_id, {"checked": [True] * len(workbench.checklist)})
    approved = workbench.approve(issue_id, {"revision": store.load()["revision"]})
    assert approved["status"]["articleState"] == "approved"

    def fake_render(article, out_dir, style="raw"):
        assert style == "card"
        out = Path(out_dir)
        image = out / f"{article.article_id}.png"
        cover = out / f"{article.article_id}-cover.jpg"
        html = out / f"{article.article_id}.html"
        Image.new("RGB", (1080, 2600), "white").save(image)
        Image.new("RGB", (900, 383), "white").save(cover)
        html.write_text("ok", encoding="utf-8")
        article.transition(ArticleState.IMAGE)
        article.image_path = str(image)
        article.cover_path = str(cover)
        article.render_style = "card"
        article.content_hash = article.export_hash = article_content_hash(article)
        return str(image)

    monkeypatch.setattr(app_module, "render_long_image", fake_render)
    exported = workbench.export(issue_id)
    assert exported["artifacts"][:2] == [f"{issue_id}.png", f"{issue_id}-cover.jpg"]
    assert len(exported["artifacts"]) == 5
    for name in ("full", "cover", *exported["artifacts"][3:]):
        assert workbench.artifact(issue_id, name).is_file()


def test_http_capability_session_review_csp_and_path_traversal(app_module, workbench):
    import http.cookiejar

    server = app_module.create_server("127.0.0.1", 0, workbench)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, method="GET", payload=None, headers=None, client=opener):
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(root + path, data=body, method=method, headers={"Content-Type": "application/json", **(headers or {})})
        with client.open(request, timeout=3) as response:
            return response.status, response.read(), response.headers

    try:
        status, body, _ = call("/healthz", client=urllib.request.build_opener())
        assert status == 200 and "capabilities" in json.loads(body)
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            call("/api/issues", client=urllib.request.build_opener())
        assert unauthorized.value.code == 401
        status, body, _ = call("/_session/mint", "POST", {"owner_id": "owner-a"}, {"X-Ribao-Capability": workbench.sessions.secret})
        code = json.loads(body)["code"]
        status, _, headers = call(f"/_session/exchange?code={code}")
        assert status == 200  # urllib follows the 303 and stores the HttpOnly cookie
        assert "HttpOnly" in headers.get("Set-Cookie", "") or opener.handlers
        with pytest.raises(urllib.error.HTTPError) as replay:
            call(f"/_session/exchange?code={code}", client=urllib.request.build_opener())
        assert replay.value.code == 401

        status, body, _ = call("/api/issues", "POST", {"date": "0915", "kind": "ribao"})
        issue_id = json.loads(body)["id"]
        assert status == 201
        status, body, headers = call(f"/issues/{issue_id}/")
        assert status == 200
        markup = body.decode()
        csp = headers["Content-Security-Policy"]
        assert "script-src 'nonce-" in csp and "style-src 'nonce-" in csp
        assert "script-src 'unsafe-inline'" not in csp
        assert "style-src-attr 'unsafe-inline'" in csp
        assert "frame-ancestors 'self' https://aime.bytedance.net https://aime.tiktok-row.net" in csp
        assert headers.get("X-Frame-Options") is None
        assert 'nonce="' in markup and f'"apiBase": "/issues/{issue_id}"' in markup
        assert "reviewTopbar" in markup
        with pytest.raises(urllib.error.HTTPError) as caught:
            call("/issues/%2e%2e/api")
        assert caught.value.code in {400, 409}
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_frontend_contract_has_all_workflow_entries():
    html = (APP_ROOT / "app/web/index.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "app/web/app.js").read_text(encoding="utf-8")
    for marker in ("materialForm", "generateButton", "reviewFrame", "exportButton", "artifacts"):
        assert f'id="{marker}"' in html
    for endpoint in ("/api/issues", "/api/materials", "/api/generate", "/api/export"):
        assert endpoint in script


def test_ribao_command_sends_dynamic_ui(monkeypatch, tmp_path, capsys):
    runtime = tmp_path / ".aime/plugin_runtime"
    runtime.mkdir(parents=True)
    (runtime / "runtime_services.json").write_text(json.dumps({"services": [{
        "plugin_name": "xiaohuoche-ribao",
        "service_name": "workbench",
        "url": "http://127.0.0.1:9911",
    }]}), encoding="utf-8")
    calls = []
    sdk = ModuleType("byted_aime_sdk")
    sdk.send_dynamic_ui = lambda **kwargs: calls.append(kwargs)
    monkeypatch.setitem(sys.modules, "byted_aime_sdk", sdk)
    data = tmp_path / "data"
    data.mkdir()
    (data / ".capability-secret").write_text("test-secret", encoding="utf-8")
    (data / ".capability-secret").chmod(0o600)
    monkeypatch.setenv("AIME_USER_ID", "owner-command-test")
    monkeypatch.setenv("AIME_APP_DATA_DIR", str(data))
    monkeypatch.setenv("AIME_WORKSPACE_PATH", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_input": {"args": "今日日报"}})))
    command = load_module("xzq_ribao_command", APP_ROOT / "commands/ribao.py")

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, _limit): return b'{"code":"one-time-code"}'

    requests = []
    monkeypatch.setattr(command, "urlopen", lambda request, timeout: requests.append(request) or Response())
    assert command.main() == 0
    assert requests[0].headers["X-ribao-capability"] == "test-secret"
    assert json.loads(requests[0].data) == {"owner_id": "owner-command-test"}
    assert calls == [{
        "uri": "http://127.0.0.1:9911/_session/exchange?code=one-time-code",
        "title": "今日日报",
        "display_mode": 1,
        "app_id": "app_786871726962616f",
    }]
    assert "已打开" in capsys.readouterr().out


def test_package_contains_vendored_runtime_without_private_assets():
    package = load_module("xzq_aime_packager", APP_ROOT / "scripts/package.py")
    output = package.build()
    with zipfile.ZipFile(output) as bundle:
        names = set(bundle.namelist())
        assert {"app.json", "app/server/main.py", "app/web/index.html", "commands/ribao.py", "engine/xzq/models.py", "engine/style/templates/ribao/page.html", "app/runtime/config.yaml"} <= names
        assert any(name.startswith("vendor/PIL/") for name in names)
        assert any(name.startswith("vendor/requests/") for name in names)
        assert "app/runtime/runtime-target.json" in names
        target = json.loads(bundle.read("app/runtime/runtime-target.json"))
        assert target == {
            "os": "linux", "arch": "x86_64", "pythonImplementation": "CPython",
            "pythonVersion": "3.11", "abi": "cp311", "wheelPlatform": "manylinux2014_x86_64",
        }
        lowered = [name.lower() for name in names]
        assert not any("darwin" in name or ".dylib" in name or "cpython-39" in name for name in lowered)
        native = [name for name in lowered if name.endswith(".so")]
        assert native and any("cpython-311" in name for name in native)
        assert not any(".dev-data" in name or "node_modules" in name or "__pycache__" in name or name.startswith("tests/") or "few_shot" in name or name.endswith("config.example.yaml") for name in names)
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)
        for name in names:
            if Path(name).suffix in {".py", ".js", ".md", ".yaml", ".json", ".txt"} and not name.startswith("vendor/"):
                payload = bundle.read(name)
                assert b"/Users/" not in payload and b"/mnt/propagation/" not in payload and b"LLM_API_KEY=" not in payload


def test_browser_workbench_create_material_generate_and_open_review(app_module, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    workbench = app_module.Workbench(tmp_path)
    server = app_module.create_server("127.0.0.1", 0, workbench)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch()
            except Exception as error:
                pytest.skip(f"Chromium 未安装：{error}")
            page = browser.new_page(viewport={"width": 390, "height": 844})
            violations = []
            page.on("console", lambda message: violations.append(message.text) if "Content Security Policy" in message.text else None)
            code = workbench.sessions.mint(workbench.sessions.secret, "owner-browser")
            page.goto(f"http://127.0.0.1:{server.server_port}/_session/exchange?code={code}")
            page.click("#newIssue")
            page.fill("#issueDate", "0916")
            page.fill("#newTitle", "0916 测试日报")
            page.click("#issueForm .primary")
            page.wait_for_selector("#workspace:not([hidden])")
            page.fill("#materialText", "今晚七点集合，骑行二十公里")
            page.click("#materialForm .primary")
            page.locator("#materialList .card").first.wait_for()
            page.click('[data-tab="generate"]')
            page.check("#offline")
            page.click("#generateButton")
            page.locator("#screenList .card").first.wait_for(state="attached")
            page.wait_for_selector("#reviewFrame")
            frame = page.frame_locator("#reviewFrame")
            frame.locator("#reviewTopbar").wait_for()
            frame.locator("#reviewStatus", has_text="已同步").wait_for()
            assert frame.locator("#reviewStatus").count() == 1
            frame.locator("#mobileReviewToggle").dispatch_event("click")
            handle = frame.locator("#reviewDragHandle")
            handle.dispatch_event("pointerdown", {"pointerId": 17, "pointerType": "touch", "clientY": 700})
            handle.dispatch_event("pointermove", {"pointerId": 17, "pointerType": "touch", "clientY": 360})
            handle.dispatch_event("pointerup", {"pointerId": 17, "pointerType": "touch", "clientY": 360})
            assert frame.locator("#reviewShell").get_attribute("data-snap") == "full"
            page.wait_for_timeout(400)  # the drag guard intentionally suppresses the immediate synthetic click
            frame.locator("#mobileAnnotate").evaluate("node => node.click()")
            frame.locator("body.annotation-mode").wait_for()
            frame.locator('[data-review-anchor="screen-01"]').dispatch_event("click", {"clientX": 40, "clientY": 40})
            frame.locator("#reviewComposer:not([hidden])").wait_for()
            frame.locator("#reviewText").fill("真实浏览器批注")
            frame.locator("#saveAnnotation").dispatch_event("click")
            frame.locator(".review-card").first.wait_for()
            frame.locator("html").evaluate("node => node.style.setProperty('--keyboard-offset', '24px')")
            assert frame.locator("html").evaluate("node => node.style.getPropertyValue('--keyboard-offset')") == "24px"
            page.wait_for_timeout(100)
            assert "离线占位内容" in page.locator("#gate").inner_text()
            assert violations == []
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_upload_validation_and_text_material_semantics(app_module, workbench):
    issue = workbench.create_issue({"date": "0917", "kind": "ribao"})
    issue_id = issue["id"]
    payload, duplicate = workbench.add_material(issue_id, {
        "name": "notes.md", "bytes": "集合地点：测试广场".encode(), "kind": "screenshot", "text": "",
    })
    assert not duplicate
    material = payload["materials"][0]
    assert material["text"] == "集合地点：测试广场"
    assert material["source"].startswith(f"materials/{issue_id}/")
    assert str(workbench.root) not in material["source"]

    for request, status_code in [
        ({"name": "bad.png", "bytes": b"not-png", "text": "说明"}, 415),
        ({"name": "bad.md", "bytes": b"\xff", "text": ""}, 422),
        ({"name": "unknown.exe", "bytes": b"MZ", "text": ""}, 415),
    ]:
        with pytest.raises(app_module.HTTPProblem) as caught:
            workbench.add_material(issue_id, request)
        assert caught.value.status_code == status_code

    from PIL import Image
    image = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(image, format="PNG")
    with pytest.raises(app_module.HTTPProblem) as missing_note:
        workbench.add_material(issue_id, {"name": "photo.png", "bytes": image.getvalue(), "text": ""})
    assert missing_note.value.status_code == 422


def test_export_manifest_detects_tamper_and_failed_commit_keeps_old(app_module, workbench, monkeypatch):
    from PIL import Image

    issue_id = draft_issue(workbench, "0918-ribao")
    store = workbench.store(issue_id)
    workbench.checklist_update(issue_id, {"checked": [True] * len(workbench.checklist)})
    workbench.approve(issue_id, {"revision": store.load()["revision"]})

    def fake_render(article, out_dir, style="raw"):
        assert style == "card"
        out = Path(out_dir)
        image = out / f"{article.article_id}.png"
        cover = out / f"{article.article_id}-cover.jpg"
        Image.new("RGB", (1080, 2300), "white").save(image)
        Image.new("RGB", (900, 383), "white").save(cover)
        (out / f"{article.article_id}.html").write_text("safe", encoding="utf-8")
        if article.state == ArticleState.APPROVED:
            article.transition(ArticleState.IMAGE)
        article.image_path, article.cover_path = str(image), str(cover)
        article.render_style = "card"
        article.content_hash = article.export_hash = article_content_hash(article)

    monkeypatch.setattr(app_module, "render_long_image", fake_render)
    first = workbench.export(issue_id)
    old_path = workbench.artifact(issue_id, "full")
    old_bytes = old_path.read_bytes()
    versions_dir = old_path.parent.parent
    old_versions = {path.name for path in versions_dir.iterdir() if path.is_dir()}

    original_save = workbench.queue.save
    def fail_new(article):
        if article.image_path != str(old_path):
            raise OSError("injected commit failure")
        return original_save(article)
    monkeypatch.setattr(workbench.queue, "save", fail_new)
    with pytest.raises(OSError, match="injected"):
        workbench.export(issue_id)
    assert workbench.artifact(issue_id, "full").read_bytes() == old_bytes
    assert workbench.article(issue_id).image_path == str(old_path)
    assert {path.name for path in versions_dir.iterdir() if path.is_dir()} == old_versions
    assert first["version"] in str(old_path)

    monkeypatch.setattr(workbench.queue, "save", original_save)
    old_path.write_bytes(b"tampered")
    assert workbench.payload(workbench.article(issue_id))["status"]["exportCurrent"] is False
    with pytest.raises(RuntimeError, match="摘要校验失败"):
        workbench.artifact(issue_id, "full")


def test_missing_export_dependency_keeps_health_capability(app_module, workbench):
    workbench.capabilities["export"] = False
    issue_id = draft_issue(workbench, "0919-ribao")
    with pytest.raises(app_module.HTTPProblem) as caught:
        workbench.export(issue_id)
    assert caught.value.status_code == 503
    assert workbench.payload(workbench.article(issue_id))["capabilities"]["export"] is False


def test_package_build_is_concurrent_and_zip_is_complete():
    package = load_module("xzq_aime_packager_concurrent", APP_ROOT / "scripts/package.py")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(lambda _: package.build(), range(2)))
    assert outputs[0] == outputs[1]
    with zipfile.ZipFile(outputs[0]) as bundle:
        assert bundle.testzip() is None
        assert "app/server/main.py" in bundle.namelist()


def test_extracted_package_starts_without_pip_or_network(tmp_path):
    if not (sys.platform.startswith("linux") and os.uname().machine.lower() in {"x86_64", "amd64"} and sys.version_info[:2] == (3, 11)):
        pytest.skip("Linux cp311 安装包只在目标 Linux x86_64 / CPython 3.11 runtime 执行")
    package = load_module("xzq_aime_packager_smoke", APP_ROOT / "scripts/package.py")
    archive = package.build()
    install = tmp_path / "installed"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(install)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    data = tmp_path / "data"
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "AIME_APP_DATA_DIR": str(data),
        "PIP_NO_INDEX": "1",
        "PYTHONPATH": "",
        "PYTHONNOUSERSITE": "1",
    }
    process = subprocess.Popen(
        ["bash", "app/server/start.sh"],
        cwd=install,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    logs: list[str] = []
    assert process.stdout is not None
    log_reader = threading.Thread(target=lambda: logs.extend(iter(process.stdout.readline, "")), daemon=True)
    log_reader.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                log_reader.join(timeout=1)
                raise AssertionError(
                    f"extracted package exited with code {exit_code} before health check:\n{''.join(logs)}"
                )
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
                    payload = json.loads(response.read())
                    assert payload["ok"] is True
                    assert payload["capabilities"]["imageUpload"] is True
                    assert "export" in payload["capabilities"]
                    break
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        else:
            raise AssertionError(
                "extracted package did not become healthy within 20 seconds; "
                f"exit code={process.returncode}; output:\n{''.join(logs)}"
            )

        import http.cookiejar
        from PIL import Image

        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        secret = (data / ".capability-secret").read_text(encoding="utf-8").strip()
        mint = urllib.request.Request(
            f"http://127.0.0.1:{port}/_session/mint",
            data=json.dumps({"owner_id": "offline-owner"}).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "X-Ribao-Capability": secret},
        )
        with opener.open(mint, timeout=3) as response:
            code = json.loads(response.read())["code"]
        with opener.open(f"http://127.0.0.1:{port}/_session/exchange?code={code}", timeout=3):
            pass
        create = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/issues",
            data=json.dumps({"id": "offline-upload", "date": "0927", "kind": "ribao"}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with opener.open(create, timeout=3):
            pass
        png = io.BytesIO()
        Image.new("RGB", (3, 3), "green").save(png, format="PNG")
        upload = urllib.request.Request(
            f"http://127.0.0.1:{port}/issues/offline-upload/api/materials",
            data=json.dumps({"name": "offline.png", "bytes": base64.b64encode(png.getvalue()).decode(), "text": "离线安装包真实图片"}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with opener.open(upload, timeout=3) as response:
            uploaded = json.loads(response.read())
        assert uploaded["issue"]["materials"][0]["source"].startswith("materials/offline-upload/")

        boundary = "----xzq-offline-upload"
        second_png = io.BytesIO()
        Image.new("RGB", (3, 3), "blue").save(second_png, format="PNG")
        multipart = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"text\"\r\n\r\nmultipart 真实图片\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"multipart.png\"\r\n"
            "Content-Type: image/png\r\n\r\n"
        ).encode() + second_png.getvalue() + f"\r\n--{boundary}--\r\n".encode()
        multipart_upload = urllib.request.Request(
            f"http://127.0.0.1:{port}/issues/offline-upload/api/materials",
            data=multipart,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with opener.open(multipart_upload, timeout=3) as response:
            multipart_result = json.loads(response.read())
        assert len(multipart_result["issue"]["materials"]) == 2
    except Exception as error:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)
        log_reader.join(timeout=2)
        raise AssertionError(f"离线包集成流程失败：{error}\n子进程日志：\n{''.join(logs)}") from error
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log_reader.join(timeout=2)


def test_command_owner_identity_prefers_runtime_id_then_jwt_and_fails_closed(monkeypatch):
    command = load_module("xzq_ribao_command_identity", APP_ROOT / "commands/ribao.py")
    keys = ("AIME_USER_ID", "AIME_ACCOUNT_ID", "IRIS_USER_ID", "IRIS_ACCOUNT_ID", "AIME_USER_CLOUD_JWT", "IRIS_USER_CLOUD_JWT")
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AIME_ACCOUNT_ID", "account-123")
    assert command.owner_id() == "account-123"
    monkeypatch.delenv("AIME_ACCOUNT_ID")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "jwt-owner"}).encode()).decode().rstrip("=")
    monkeypatch.setenv("AIME_USER_CLOUD_JWT", f"header.{payload}.signature")
    assert command.owner_id() == "jwt-owner"
    monkeypatch.delenv("AIME_USER_CLOUD_JWT")
    with pytest.raises(RuntimeError, match="稳定用户身份"):
        command.owner_id()


def test_two_session_owners_are_isolated_over_http(app_module, tmp_path):
    import http.cookiejar

    workbench = app_module.Workbench(tmp_path, export_available=True)
    server = app_module.create_server("127.0.0.1", 0, workbench)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"

    def login(owner_id):
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        body = json.dumps({"owner_id": owner_id}).encode()
        request = urllib.request.Request(root + "/_session/mint", data=body, method="POST", headers={
            "Content-Type": "application/json", "X-Ribao-Capability": workbench.sessions.secret,
        })
        with opener.open(request) as response:
            code = json.loads(response.read())["code"]
        with opener.open(root + f"/_session/exchange?code={code}"):
            pass
        return opener

    def call(opener, path, method="GET", payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(root + path, data=body, method=method, headers={"Content-Type": "application/json"})
        with opener.open(request, timeout=3) as response:
            return response.status, json.loads(response.read() or b"{}")

    try:
        alice, bob = login("alice"), login("bob")
        status, issue = call(alice, "/api/issues", "POST", {"date": "0920", "kind": "ribao"})
        assert status == 201
        issue_id = issue["id"]
        assert call(alice, "/api/issues")[1][0]["id"] == issue_id
        assert call(bob, "/api/issues")[1] == []
        for path, method, payload in (
            (f"/issues/{issue_id}/api", "GET", None),
            (f"/issues/{issue_id}/api/review", "GET", None),
            (f"/issues/{issue_id}/api/review", "PUT", {"revision": 0}),
            (f"/issues/{issue_id}/api/materials", "POST", {"text": "越权修改"}),
            (f"/issues/{issue_id}/api/artifacts/full", "GET", None),
        ):
            with pytest.raises(urllib.error.HTTPError) as denied:
                call(bob, path, method, payload)
            assert denied.value.code in {404, 409}
        marker = workbench.queue_for("alice").work_dir / issue_id / "owner.json"
        assert json.loads(marker.read_text())["ownerHash"] == workbench.owner_key("alice")
        assert not (workbench.queue_for("bob").work_dir / f"{issue_id}.json").exists()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_export_capability_probe_fails_closed_and_health_survives(app_module, monkeypatch, tmp_path):
    original = app_module.importlib.util.find_spec

    def guarded(name):
        if name == "PIL":
            return original(name)
        if name == "playwright":
            return None
        if name == "playwright.sync_api":
            raise ModuleNotFoundError("No module named 'playwright'")
        return original(name)

    monkeypatch.setattr(app_module.importlib.util, "find_spec", guarded)
    assert app_module.export_capable() is False
    workbench = app_module.Workbench(tmp_path)
    server = app_module.create_server("127.0.0.1", 0, workbench)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/healthz", timeout=3) as response:
            health = json.loads(response.read())
        assert health == {"ok": True, "capabilities": {"imageUpload": True, "export": False}}
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_same_issue_export_is_mutually_exclusive(app_module, workbench, monkeypatch):
    from PIL import Image

    issue_id = draft_issue(workbench, "0921-ribao")
    store = workbench.store(issue_id)
    workbench.checklist_update(issue_id, {"checked": [True] * len(workbench.checklist)})
    workbench.approve(issue_id, {"revision": store.load()["revision"]})
    entered = threading.Event()
    release = threading.Event()

    def blocking_render(article, out_dir, style="raw"):
        assert style == "card"
        entered.set()
        assert release.wait(5)
        out = Path(out_dir)
        image, cover = out / f"{article.article_id}.png", out / f"{article.article_id}-cover.jpg"
        Image.new("RGB", (100, 100), "white").save(image)
        Image.new("RGB", (90, 40), "white").save(cover)
        article.transition(ArticleState.IMAGE)
        article.image_path, article.cover_path, article.render_style = str(image), str(cover), "card"
        article.content_hash = article.export_hash = article_content_hash(article)

    monkeypatch.setattr(app_module, "render_long_image", blocking_render)
    workbench._export_slots = threading.BoundedSemaphore(1)
    assert workbench._export_slots.acquire(blocking=False)
    try:
        with pytest.raises(app_module.HTTPProblem) as saturated:
            workbench.export(issue_id)
        assert saturated.value.status_code == 429
    finally:
        workbench._export_slots.release()
    result = []
    worker = threading.Thread(target=lambda: result.append(workbench.export(issue_id)))
    worker.start()
    assert entered.wait(3)
    with pytest.raises(app_module.HTTPProblem) as busy:
        workbench.export(issue_id)
    assert busy.value.status_code == 409
    release.set(); worker.join(timeout=8)
    assert not worker.is_alive() and len(result) == 1


def test_production_exchange_cookie_supports_partitioned_iframe(app_module, workbench):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    server = app_module.create_server("127.0.0.1", 0, workbench)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server_url = f"http://127.0.0.1:{server.server_port}"
    try:
        code = workbench.sessions.mint(workbench.sessions.secret, "cookie-owner")
        request = urllib.request.Request(
            server_url + f"/_session/exchange?code={code}",
            headers={"X-Forwarded-Proto": "https", "Host": "app.aime-plugin.bytedance.net"},
        )
        with pytest.raises(urllib.error.HTTPError) as redirect:
            urllib.request.build_opener(NoRedirect).open(request)
        assert redirect.value.code == 303
        cookie = redirect.value.headers["Set-Cookie"]
        assert all(item in cookie for item in ("HttpOnly", "SameSite=None", "Secure", "Partitioned"))
        assert redirect.value.headers.get("X-Frame-Options") is None
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_owner_cumulative_quota_rate_limit_and_instance_high_watermark(app_module, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "MAX_OWNER_ISSUES", 2)
    monkeypatch.setattr(app_module, "CREATE_RATE_LIMIT", 10)
    workbench = app_module.Workbench(tmp_path / "count", export_available=False)
    workbench.create_issue({"id": "one", "date": "0922", "kind": "ribao"}, "quota-owner")
    workbench.create_issue({"id": "two", "date": "0923", "kind": "ribao"}, "quota-owner")
    with pytest.raises(app_module.HTTPProblem) as issue_limit:
        workbench.create_issue({"id": "three", "date": "0924", "kind": "ribao"}, "quota-owner")
    assert issue_limit.value.status_code == 429

    monkeypatch.setattr(app_module, "CREATE_RATE_LIMIT", 1)
    rate_limited = app_module.Workbench(tmp_path / "rate", export_available=False)
    rate_limited.create_issue({"id": "one", "date": "0922", "kind": "ribao"}, "rate-owner")
    with pytest.raises(app_module.HTTPProblem) as rate:
        rate_limited.create_issue({"id": "two", "date": "0923", "kind": "ribao"}, "rate-owner")
    assert rate.value.status_code == 429

    monkeypatch.setattr(app_module, "CREATE_RATE_LIMIT", 10)
    monkeypatch.setattr(app_module, "UPLOAD_RATE_LIMIT", 10)
    storage = app_module.Workbench(tmp_path / "storage", export_available=False)
    storage.create_issue({"id": "one", "date": "0922", "kind": "ribao"}, "storage-owner")
    storage.create_issue({"id": "two", "date": "0923", "kind": "ribao"}, "storage-owner")
    storage.add_material("one", {"text": "first"}, "storage-owner")
    used = storage._tree_bytes(storage.owner_root("storage-owner"))
    monkeypatch.setattr(app_module, "MAX_OWNER_BYTES", used + 4096 + 10)
    with pytest.raises(app_module.HTTPProblem) as cumulative:
        storage.add_material("two", {"text": "this pushes the owner over the cumulative quota"}, "storage-owner")
    assert cumulative.value.status_code == 507

    monkeypatch.setattr(app_module, "MAX_OWNER_BYTES", 1024 * 1024)
    high_water = app_module.Workbench(tmp_path / "high-water", export_available=False)
    monkeypatch.setattr(app_module, "MAX_DATA_ROOT_BYTES", high_water._tree_bytes(high_water.root) + 1)
    with pytest.raises(app_module.HTTPProblem) as full:
        high_water.create_issue({"id": "one", "date": "0922", "kind": "ribao"}, "owner")
    assert full.value.status_code == 507


def test_quota_scan_excludes_install_and_vendor(app_module, monkeypatch, tmp_path):
    # Even a bad deployment that points AIME_APP_DATA_DIR at the install root
    # must never traverse packaged wheels for every upload.
    install_like_root = tmp_path / "installed"
    vendor = install_like_root / "vendor" / "PIL"
    vendor.mkdir(parents=True)
    (vendor / "large-native.so").write_bytes(b"x" * (2 * 1024 * 1024))
    monkeypatch.setattr(app_module, "MAX_DATA_ROOT_BYTES", 1024 * 1024)
    workbench = app_module.Workbench(install_like_root, export_available=False)
    issue = workbench.create_issue({"id": "quota-scope", "date": "0928", "kind": "ribao"})
    workbench.add_material(issue["id"], {"text": "quota only scans managed user data"})
    assert workbench._tree_bytes(workbench.root) > app_module.MAX_DATA_ROOT_BYTES
    assert workbench._tree_bytes(workbench.users_dir) < app_module.MAX_DATA_ROOT_BYTES


def test_runtime_launcher_declares_and_guards_linux_cp311_target():
    target = json.loads((APP_ROOT / "app/runtime/runtime-target.json").read_text(encoding="utf-8"))
    launcher = (APP_ROOT / "app/server/start.sh").read_text(encoding="utf-8")
    assert target["os"] == "linux" and target["arch"] == "x86_64" and target["abi"] == "cp311"
    for marker in ('sys.version_info[:2] == (3, 11)', 'sys.platform.startswith("linux")', '"x86_64"', "exit 42"):
        assert marker in launcher


def test_material_reference_is_portable_resolved_and_export_current(app_module, monkeypatch, tmp_path):
    from PIL import Image
    from xzq.renderer.to_longimage import render_html

    workbench = app_module.Workbench(tmp_path / "data", export_available=True)
    issue_id = workbench.create_issue({"id": "portable", "date": "0925", "kind": "ribao"})["id"]
    image_bytes = io.BytesIO()
    Image.new("RGB", (12, 8), "red").save(image_bytes, format="PNG")
    workbench.add_material(issue_id, {"name": "ride.png", "bytes": image_bytes.getvalue(), "text": "真实骑行合影", "kind": "photo"})
    article = workbench.article(issue_id)
    stored_source = article.materials[0].source
    assert stored_source == f"materials/{issue_id}/" + Path(stored_source).name
    article.state = ArticleState.DRAFT
    article.screens = [Screen("cover", ["真实正文"]), Screen("end", ["收车"])]
    article.review_items = list(workbench.checklist)
    article.review_checked = [True] * len(workbench.checklist)
    workbench.queue.save(article)
    workbench.approve(issue_id, {"revision": workbench.store(issue_id).load()["revision"]})

    def fake_render(resolved, out_dir, style="raw"):
        assert style == app_module.RENDER_STYLE == "card"
        source = Path(resolved.materials[0].source)
        assert source.is_absolute() and source.is_file()
        markup = render_html(resolved)
        assert source.resolve().as_uri() in markup and "<img " in markup
        out = Path(out_dir)
        html_path = out / f"{resolved.article_id}.html"
        image_path = out / f"{resolved.article_id}.png"
        cover_path = out / f"{resolved.article_id}-cover.jpg"
        html_path.write_text(markup, encoding="utf-8")
        Image.new("RGB", (1080, 2300), "white").save(image_path)
        Image.new("RGB", (900, 383), "white").save(cover_path)
        resolved.transition(ArticleState.IMAGE)
        resolved.image_path, resolved.cover_path = str(image_path), str(cover_path)
        resolved.render_style = style
        resolved.content_hash = resolved.export_hash = article_content_hash(resolved, style)

    monkeypatch.chdir(tmp_path / "data")
    monkeypatch.setattr(app_module, "render_long_image", fake_render)
    workbench.export(issue_id)
    payload = workbench.payload(workbench.article(issue_id))
    assert payload["status"]["exportCurrent"] is True
    assert payload["artifacts"]["full"] and payload["artifacts"]["cover"]
    assert workbench.artifact(issue_id, "full").is_file()
    assert workbench.article(issue_id).materials[0].source == stored_source

    escaped = workbench.article(issue_id)
    escaped.materials[0].source = "../outside.png"
    workbench.queue.save(escaped)
    with pytest.raises(app_module.HTTPProblem) as traversal:
        workbench._resolved_article(workbench.article(issue_id), app_module.DEFAULT_OWNER)
    assert traversal.value.status_code == 422


def test_missing_pillow_disables_image_upload_with_503(app_module, tmp_path):
    from PIL import Image

    workbench = app_module.Workbench(tmp_path, export_available=False)
    workbench.capabilities["imageUpload"] = False
    issue_id = workbench.create_issue({"id": "no-pillow", "date": "0926", "kind": "ribao"})["id"]
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(image, format="PNG")
    with pytest.raises(app_module.HTTPProblem) as missing:
        workbench.add_material(issue_id, {"name": "photo.png", "bytes": image.getvalue(), "text": "说明"})
    assert missing.value.status_code == 503
    assert workbench.payload(workbench.article(issue_id))["capabilities"]["imageUpload"] is False
