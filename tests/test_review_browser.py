from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from xzq.models import Article, ArticleState, Screen
from xzq.reviewer.queue import ArticleQueue
from xzq.reviewer.server import make_preview_handler


def test_browser_create_resolve_reopen_edit_delete_and_mobile(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    queue = ArticleQueue(tmp_path)
    article = Article("0914-ribao", "ribao", "0914", state=ArticleState.DRAFT, screens=[Screen("cover", ["可审阅正文"])])
    queue.save(article)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_preview_handler(queue, article.article_id, []))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch()
            except Exception as error:
                pytest.skip(f"Chromium 未安装：{error}")
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.wait_for_function("document.querySelector('#reviewStatus').textContent !== '正在连接服务'")
            page.click("#mobileReviewToggle")
            page.wait_for_function("document.querySelector('#reviewShell').classList.contains('open')")
            assert "open" in page.locator("#reviewShell").get_attribute("class")
            page.keyboard.press("c")
            page.locator('[data-review-anchor="screen-01"]').click(position={"x": 80, "y": 80})
            page.fill("#reviewText", "标题再短一点")
            page.keyboard.press("Control+Enter")
            page.wait_for_function("document.querySelectorAll('.review-card').length === 1")
            assert "标题再短一点" in page.locator(".review-card").inner_text()
            page.get_by_role("button", name="解决", exact=True).click()
            page.get_by_role("button", name="已解决").click()
            assert page.get_by_role("button", name="重开").count() == 1
            page.get_by_role("button", name="重开").click()
            page.get_by_role("button", name="待处理").click()
            page.on("dialog", lambda dialog: dialog.accept("改后的批注"))
            page.get_by_role("button", name="编辑").click()
            page.wait_for_function("document.querySelector('.review-card p').textContent === '改后的批注'")
            page.get_by_role("button", name="删除").click()
            page.wait_for_function("document.querySelectorAll('.review-card').length === 0")
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_browser_debounce_blocks_sse_overwrite_and_sync_failure_keeps_local_value(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    queue = ArticleQueue(tmp_path)
    article = Article("dirty-ribao", "ribao", "0914", state=ArticleState.DRAFT, screens=[Screen("cover", ["正文"])])
    queue.save(article)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_preview_handler(queue, article.article_id, []))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch()
            except Exception as error:
                pytest.skip(f"Chromium 未安装：{error}")
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.wait_for_function("typeof document.querySelector('#reviewGeneralNote').oninput === 'function'")
            page.evaluate("document.querySelector('#reviewNote').hidden = false")
            with page.expect_response(
                lambda response: response.url.endswith("/api/review")
                and response.request.method == "PUT"
                and response.status == 409
            ):
                page.fill("#reviewGeneralNote", "防抖期间的本地输入")
                page.evaluate("""async () => {
                  const current = await (await fetch('/api/review')).json();
                  current.generalNote = '远端并发值';
                  await fetch('/api/review', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(current)});
                }""")
            page.wait_for_timeout(1100)
            assert page.input_value("#reviewGeneralNote") == "防抖期间的本地输入"
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_browser_checklist_and_invalid_import_rollback(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    import xzq.reviewer.server as server_module

    original_status_payload = server_module.status_payload
    delay_status = threading.Event()
    status_entered = threading.Event()
    release_status = threading.Event()

    def controlled_status(*args, **kwargs):
        payload = original_status_payload(*args, **kwargs)
        if delay_status.is_set() and not status_entered.is_set():
            status_entered.set()
            assert release_status.wait(3)
        return payload

    monkeypatch.setattr(server_module, "status_payload", controlled_status)
    queue = ArticleQueue(tmp_path)
    article = Article("check-browser", "ribao", "0914", state=ArticleState.DRAFT, screens=[Screen("cover", ["正文"])])
    queue.save(article)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_preview_handler(queue, article.article_id, ["事实准确", "授权完成"]))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch()
            except Exception as error:
                pytest.skip(f"Chromium 未安装：{error}")
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.wait_for_function("document.querySelectorAll('#reviewChecklist input').length === 2")
            # 制造一个先发、后返回的 /api/status；checklist POST 与所有状态刷新共享队列，
            # 最终 UI 必须保持 POST 的新结果而不是旧 GET 快照。
            delay_status.set()
            changed = queue.get(article.article_id); changed.updated_at += 1; queue.save(changed)
            assert status_entered.wait(3)
            page.locator('.review-main-actions [data-view="checklist"]').click()
            page.locator("#reviewChecklist input").nth(0).check()
            page.locator("#reviewChecklist input").nth(1).check()
            release_status.set()
            page.wait_for_function("!document.querySelector('#approveReview').disabled")
            page.wait_for_function("document.querySelector('#reviewStatus').textContent === '已同步'")
            page.wait_for_function("document.querySelector('#reviewGate').textContent.startsWith('✓')")
            assert page.locator("#reviewChecklist input:checked").count() == 2
            page.locator('.review-main-actions [data-view="submit"]').click()
            page.click("#approveReview")
            page.wait_for_selector("#approvalDialog:not([hidden])")
            assert "当前版本" in page.locator("#approvalDialogSummary").inner_text()
            assert "成品状态" in page.locator("#approvalDialogSummary").inner_text()
            with page.expect_response(lambda response: response.url.endswith("/api/review/approve") and response.status == 200):
                page.click("#confirmApproval")
            assert queue.get(article.article_id).state == ArticleState.APPROVED

            invalid = {"name": "invalid.json", "mimeType": "application/json", "buffer": b'{"schemaVersion":1,"annotations":[{"id":"bad"}]}'}
            page.set_input_files("#importReview", invalid)
            page.wait_for_function("document.querySelector('#reviewStatus').textContent.startsWith('导入失败：')")
            assert page.locator(".review-card").count() == 0
            assert queue.get(article.article_id).state == ArticleState.APPROVED
            browser.close()
    finally:
        release_status.set(); server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_browser_mobile_long_press_quick_tag_offline_queue_and_undo(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    queue = ArticleQueue(tmp_path)
    article = Article(
        "mobile-ribao",
        "ribao",
        "0914",
        state=ArticleState.DRAFT,
        screens=[Screen("cover", ["适合手机长按审阅的正文"]), Screen("正文", ["第二屏内容"])],
    )
    queue.save(article)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_preview_handler(queue, article.article_id, ["事实准确"]))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch()
            except Exception as error:
                pytest.skip(f"Chromium 未安装：{error}")
            context = browser.new_context(viewport={"width": 375, "height": 812}, has_touch=True)
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.wait_for_function("typeof document.querySelector('#mobileAnnotate').onclick === 'function'")
            assert page.locator("#reviewBottomBar").is_visible()
            assert page.locator("#reviewShell").get_attribute("data-snap") == "peek"
            assert page.locator("#reviewTitle").inner_text() == "mobile 日报"
            assert page.locator("#mobileOpinionCount").inner_text() == "0"

            anchor = page.locator('[data-review-anchor="screen-01"]')
            box = anchor.bounding_box()
            assert box
            point = {"clientX": box["x"] + 40, "clientY": box["y"] + 40, "pointerType": "touch", "pointerId": 7}
            anchor.dispatch_event("pointerdown", point)
            page.wait_for_timeout(450)
            anchor.dispatch_event("pointerup", point)
            assert page.locator("#reviewComposer").is_visible()
            page.get_by_role("button", name="错字", exact=True).click()
            assert page.input_value("#reviewText") == "错字"

            context.set_offline(True)
            page.click("#saveAnnotation")
            page.wait_for_function("document.querySelectorAll('.review-card').length === 1")
            assert page.locator("#mobileOpinionCount").inner_text() == "1"
            assert page.get_by_role("button", name="最近").count() == 0
            assert page.locator("#reviewRecentNotes button").first.inner_text() == "错字"
            page.wait_for_function("document.querySelector('#reviewStatus').textContent === '离线修改已暂存'")
            assert page.locator("#generateRevision").is_disabled()
            assert page.evaluate("Object.keys(localStorage).some(key => key.endsWith(':pending') && JSON.parse(localStorage[key]).length === 1)")

            context.set_offline(False)
            page.wait_for_function("Object.keys(localStorage).some(key => key.endsWith(':pending') && JSON.parse(localStorage[key]).length === 0)")
            page.wait_for_function("document.querySelector('#reviewStatus').textContent === '已同步'")

            page.on("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="删除", exact=True).click()
            assert page.locator("#reviewUndo").is_visible()
            page.click("#undoDelete")
            page.wait_for_function("document.querySelectorAll('.review-card').length === 1")
            assert page.locator(".review-card").get_attribute("data-anchor") == "screen-01"
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_browser_mobile_viewports_safe_area_and_landscape_split(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    queue = ArticleQueue(tmp_path)
    article = Article("responsive-ribao", "ribao", "0914", state=ArticleState.DRAFT, screens=[Screen("cover", ["正文"])] )
    queue.save(article)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_preview_handler(queue, article.article_id, []))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch()
            except Exception as error:
                pytest.skip(f"Chromium 未安装：{error}")
            for width in (320, 375, 390, 430):
                page = browser.new_page(viewport={"width": width, "height": 844})
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.wait_for_function("document.body.classList.contains('review-enabled')")
                metrics = page.evaluate("""() => ({
                  bodyOverflow: document.documentElement.scrollWidth <= innerWidth,
                  bottomHeight: document.querySelector('#reviewBottomBar').getBoundingClientRect().height,
                  annotateHeight: document.querySelector('#mobileAnnotate').getBoundingClientRect().height,
                  articleColor: getComputedStyle(document.querySelector('#article-content')).backgroundColor,
                  topActionHeight: document.querySelector('#reviewMore').getBoundingClientRect().height
                })""")
                assert metrics["bodyOverflow"]
                assert metrics["bottomHeight"] >= 52
                assert metrics["annotateHeight"] >= 44
                assert metrics["articleColor"] != "rgb(17, 17, 17)"
                assert metrics["topActionHeight"] >= 40
                if width == 390:
                    page.evaluate("document.body.style.minHeight = '2000px'; scrollTo(0, 500)")
                    page.wait_for_function("document.body.classList.contains('review-controls-hidden')")
                    page.evaluate("scrollTo(0, 0)")
                    page.wait_for_function("!document.body.classList.contains('review-controls-hidden')")
                page.close()
            page = browser.new_page(viewport={"width": 844, "height": 390})
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.wait_for_function("typeof document.querySelector('#mobileReviewToggle').onclick === 'function'")
            page.dispatch_event("#mobileReviewToggle", "click")
            page.wait_for_function("document.querySelector('#reviewShell').classList.contains('open')")
            widths = page.evaluate("""() => ({
              body: document.body.getBoundingClientRect().width,
              shell: document.querySelector('#reviewShell').getBoundingClientRect().width
            })""")
            assert abs(widths["body"] / 844 - 0.65) < 0.03
            assert abs(widths["shell"] / 844 - 0.35) < 0.03
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_browser_same_annotation_conflict_can_choose_remote(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    queue = ArticleQueue(tmp_path)
    article = Article("conflict-mobile", "ribao", "0914", state=ArticleState.DRAFT, screens=[Screen("cover", ["冲突正文"])])
    queue.save(article)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_preview_handler(queue, article.article_id, []))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch()
            except Exception as error:
                pytest.skip(f"Chromium 未安装：{error}")
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(base_url)
            page.wait_for_function("typeof document.querySelector('#mobileAnnotate').onclick === 'function'")
            page.click("#mobileAnnotate")
            page.locator('[data-review-anchor="screen-01"]').click(position={"x": 60, "y": 60})
            page.fill("#reviewText", "初始版本")
            page.click("#saveAnnotation")
            page.wait_for_function("document.querySelector('#reviewStatus').textContent === '已同步'")

            context.set_offline(True)
            page.on("dialog", lambda dialog: dialog.accept("我的离线版本"))
            page.get_by_role("button", name="编辑", exact=True).click()
            page.wait_for_function("document.querySelector('.review-card p').textContent === '我的离线版本'")

            with urllib.request.urlopen(f"{base_url}/api/review") as response:
                remote = json.load(response)
            remote["annotations"][0]["text"] = "远端版本"
            remote["annotations"][0]["updatedAt"] = "2099-01-01T00:00:00Z"
            request = urllib.request.Request(
                f"{base_url}/api/review",
                data=json.dumps(remote).encode(),
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            with urllib.request.urlopen(request) as response:
                assert response.status == 200

            context.set_offline(False)
            page.wait_for_function("!document.querySelector('#reviewConflicts').hidden")
            page.get_by_role("button", name="采用远端版本", exact=False).click()
            page.wait_for_function("document.querySelector('.review-card p').textContent === '远端版本'")
            assert page.locator("#reviewConflicts").is_hidden()
            assert page.evaluate("Object.keys(localStorage).some(key => key.endsWith(':pending') && JSON.parse(localStorage[key]).length === 0)")
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_browser_mobile_gestures_swipe_undo_and_approval_modal(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    queue = ArticleQueue(tmp_path)
    article = Article(
        "mobile-interactions",
        "ribao",
        "0914",
        state=ArticleState.DRAFT,
        screens=[Screen("cover", ["移动交互正文"])],
    )
    queue.save(article)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_preview_handler(queue, article.article_id, ["事实准确"]))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch()
            except Exception as error:
                pytest.skip(f"Chromium 未安装：{error}")
            context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.wait_for_function("typeof document.querySelector('#mobileAnnotate').onclick === 'function'")

            handle = page.locator("#reviewDragHandle")
            for name, y in (("pointerdown", 600), ("pointermove", 490), ("pointerup", 490)):
                handle.dispatch_event(name, {"pointerId": 31, "pointerType": "touch", "clientX": 190, "clientY": y})
            handle.dispatch_event("click")
            assert page.locator("#reviewShell").get_attribute("data-snap") == "full"

            for name, y in (("pointerdown", 400), ("pointermove", 550), ("pointerup", 550)):
                handle.dispatch_event(name, {"pointerId": 32, "pointerType": "touch", "clientX": 190, "clientY": y})
            handle.dispatch_event("click")
            assert page.locator("#reviewShell").get_attribute("data-snap") == "peek"

            page.click("#mobileAnnotate")
            page.locator('[data-review-anchor="screen-01"]').click(position={"x": 60, "y": 60})
            page.fill("#reviewText", "滑动测试")
            page.click("#saveAnnotation")
            page.wait_for_function("document.querySelectorAll('.review-card').length === 1")

            def swipe(points, pointer_id):
                card = page.locator(".review-card")
                card.dispatch_event("pointerdown", {"pointerId": pointer_id, "pointerType": "touch", "clientX": points[0][0], "clientY": points[0][1]})
                card.dispatch_event("pointermove", {"pointerId": pointer_id, "pointerType": "touch", "clientX": points[1][0], "clientY": points[1][1]})
                card.dispatch_event("pointerup", {"pointerId": pointer_id, "pointerType": "touch", "clientX": points[1][0], "clientY": points[1][1]})

            swipe(((80, 300), (140, 305)), 41)
            assert page.locator(".review-card").count() == 1
            swipe(((80, 300), (120, 410)), 42)
            assert page.locator(".review-card").count() == 1
            swipe(((80, 300), (180, 308)), 43)
            page.wait_for_function("document.querySelectorAll('.review-card').length === 0")
            assert page.locator("#reviewUndo").is_visible()
            assert "批注已解决" in page.locator("#reviewUndo").inner_text()
            page.click("#undoDelete")
            page.wait_for_function("document.querySelectorAll('.review-card').length === 1")
            page.wait_for_function("Object.keys(localStorage).some(key => key.endsWith(':pending') && JSON.parse(localStorage[key]).length === 0)")

            page.get_by_role("button", name="解决", exact=True).click()
            page.dispatch_event("#mobileChecklist", "click")
            page.locator("#reviewChecklist input").check()
            page.wait_for_function("document.querySelector('#reviewGate').textContent.startsWith('✓')")
            page.dispatch_event("#mobileSubmit", "click")
            page.click("#approveReview")
            page.wait_for_selector("#approvalDialog:not([hidden])")
            assert page.evaluate("document.activeElement.id") == "confirmApproval"
            assert page.evaluate("[...document.body.children].filter(node => node.id !== 'approvalDialog' && !['SCRIPT', 'STYLE'].includes(node.tagName)).every(node => node.inert)")
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement.id") == "cancelApproval"
            page.keyboard.press("Shift+Tab")
            assert page.evaluate("document.activeElement.id") == "confirmApproval"
            page.keyboard.press("Escape")
            assert page.locator("#approvalDialog").is_hidden()
            assert page.evaluate("document.activeElement.id") == "approveReview"
            assert not page.evaluate("document.querySelector('#reviewShell').inert || document.querySelector('#article-content').inert")
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
