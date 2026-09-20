#!/usr/bin/env python3
"""辛庄桥小火车 AI 推文流水线命令行。

子命令：
  collect   采集素材（Strava + 素材筐图片 OCR），建/更新稿件到 inbox
  generate  生成分屏脚本（--offline 用模板骨架，无需 LLM）
  list      列出稿件及状态
  approve   人工审核通过（需 checklist 全勾）
  reject    打回补素材
  render    渲染 1080 长图（需 playwright）
  push      推进微信草稿箱（需公众号密钥；个人号到此为止，手机人工群发）
  ribao-check  一次检查日报标题、素材、few-shot、封面与导出 PNG
  run       一键跑 collect→generate（离线可用），后续人工审核

示例：
  python pipeline.py run --offline --date 0913 --kind ribao
  python pipeline.py collect --date 0913 --kind wanbao
  python pipeline.py approve <article_id>
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

from xzq.collector.inbox import list_inbox_images
from xzq.collector.strava import StravaClient
from xzq.collector.transcribe import transcribe_materials
from xzq.config import Config
from xzq.llm import LLMClient
from xzq.models import Article, ArticleState, Material
from xzq.publisher.draft import push_draft
from xzq.publisher.token import TokenManager
from xzq.renderer.network_stickers import NetworkStickerCatalog
from xzq.renderer.to_longimage import (
    article_content_hash,
    render_html,
    render_long_image,
)
from xzq.reviewer.queue import ArticleQueue
from xzq.reviewer.server import make_preview_handler
from xzq.reviewer.watch import watch_materials
from xzq.ribao_gate import validate_rendered_ribao, validate_ribao_case
from xzq.ribao_audit import audit_ribao
from xzq.writer.generate import generate_script
from xzq.writer.style import StyleAssets


def _subject_safety_capability_errors(face_model: Path | None = None) -> list[str]:
    """Return every missing release-critical subject-safety capability."""
    from xzq.renderer.faces import MODEL

    errors = []
    for module in ("rembg", "cv2", "playwright.sync_api"):
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            available = False
        if not available:
            errors.append(f"缺少 Python 模块 {module}")
    model = Path(face_model or MODEL)
    if not model.is_file():
        errors.append(f"缺少 YuNet 模型 {model}")
    return errors


def _ctx(args):
    cfg = Config.load(args.config)
    work_dir = cfg.path("work_dir", "./data/articles")
    queue = ArticleQueue(work_dir)
    assets = StyleAssets.load(cfg.path("style_dir", "./style"))
    llm = LLMClient()
    return cfg, queue, assets, llm


def _article_id(date: str, kind: str) -> str:
    return f"{date}-{kind}"


def _merge_materials(
    _existing: list[Material], incoming: list[Material]
) -> list[Material]:
    """把本轮采集结果作为完整快照；同一来源以本轮最后一条为准。"""
    merged: list[Material] = []
    positions: dict[tuple[str, str, str], int] = {}

    def identity(material: Material) -> tuple[str, str, str]:
        fingerprint = str(material.meta.get("content_sha256", ""))
        if fingerprint:
            return "image", "sha256", fingerprint
        source = material.source or str(material.meta.get("source", ""))
        if source:
            return material.kind, "source", source
        return material.kind, "content", material.text

    for material in incoming:
        key = identity(material)
        if key in positions:
            merged[positions[key]] = material
        else:
            positions[key] = len(merged)
            merged.append(material)
    return merged


def _apply_collected_materials(article: Article, incoming: list[Material]) -> bool:
    """合并采集结果；仅在确有新素材时让已有草稿失效。"""
    merged = _merge_materials(article.materials, incoming)
    if merged == article.materials:
        return False
    article.materials = merged
    if article.state == ArticleState.DRAFT:
        article.transition(ArticleState.INBOX)
    article.screens = []
    article.review_checked = []
    return True


def cmd_collect(args) -> None:
    cfg, queue, _assets, llm = _ctx(args)
    materials: list[Material] = []

    # 1) 素材筐图片（手动投喂，合规）
    inbox_dir = cfg.path("inbox_dir", "./data/inbox")
    materials.extend(list_inbox_images(inbox_dir))
    # 2) Strava 硬数据（官方 API，合规自动）：俱乐部活动 + 最近路段 PR
    strava = StravaClient(
        club_id=cfg.get("strava", "club_id"),
        club_url=cfg.get("strava", "club_url"),
    )
    if strava.is_configured():
        try:
            materials.extend(strava.collect_all())
        except Exception as e:
            print(f"[warn] Strava 拉取失败：{e}")
    else:
        print("[info] 未配置 Strava，跳过硬数据采集")

    # OCR 截图/照片
    transcribe_materials(materials, llm)

    aid = _article_id(args.date, args.kind)
    if queue.exists(aid):
        article = queue.get(aid)
        if article.state not in (ArticleState.INBOX, ArticleState.DRAFT):
            raise RuntimeError(
                f"稿件已进入 {article.state.value}，不能覆盖采集；请新建日期/栏目或先打回"
            )
        _apply_collected_materials(article, materials)
    else:
        article = Article(
            article_id=aid, kind=args.kind, date=args.date, materials=materials
        )
    queue.save(article)
    print(
        f"[ok] 采集完成 {aid}：当前共 {len(article.materials)} 条素材 -> {queue.work_dir / (aid + '.json')}"
    )


def cmd_generate(args) -> None:
    cfg, queue, assets, llm = _ctx(args)
    article = queue.get(_article_id(args.date, args.kind))
    generate_script(article, assets, llm, offline=args.offline)
    article.review_items = list(cfg.review_checklist)
    article.review_checked = [False] * len(cfg.review_checklist)
    queue.save(article)
    print(
        f"[ok] 分屏脚本已生成：{article.title}（{len(article.screens)} 屏，"
        f"{'离线骨架' if args.offline or not llm.is_configured() else 'LLM'}）"
    )
    for w in article.gen_warnings:
        print(f"      ⚠️  {w}")
    if not article.gen_warnings:
        print("      自检通过（抄样例 / 官腔 / 用词 / 贴纸名 0 条）")
    if not getattr(args, "auto", False):
        print(
            "      下一步：人工审核后 python3 pipeline.py approve", article.article_id
        )


def _http_status(error: Exception) -> int | None:
    """只提取 HTTP 状态码，避免 doctor 把上游响应或凭证打印到终端。"""
    return getattr(getattr(error, "response", None), "status_code", None)


def _strava_failure_hint(error: Exception) -> str:
    status = _http_status(error)
    if status in (400, 401):
        return "refresh_token 已失效或与 Client 不匹配，请重新完成 Strava OAuth 授权"
    if status == 403:
        return (
            "授权被拒绝，可能是账号限制、凭证不匹配或 scope 不足；"
            "请核对 Client 与账号后重新授权"
        )
    if status is None:
        return "请求未获得 HTTP 响应，请检查本机网络、DNS 或代理后重试"
    return f"Strava 返回 HTTP {status}，请核对账号授权与 Club 可见性"


def _strava_check_failure_hint(name: str, error: Exception) -> str:
    status = _http_status(error)
    if status is None:
        return "请求未获得 HTTP 响应，请检查本机网络、DNS 或代理"
    if name == "本人活动/PR scope" and status in (401, 403):
        return (
            "授权 scope 不足，请使用 read,profile:read_all,activity:read_all 重新授权"
        )
    if name == "私密 Club 活动" and status in (403, 404):
        return "当前授权账号看不到该 Club，请确认已加入俱乐部且 Club ID 正确"
    return f"Strava 返回 HTTP {status}"


def cmd_doctor(args) -> None:
    """一键检查外部集成，不打印任何密钥；已配置项探活失败时返回非零。"""
    cfg, _queue, _assets, llm = _ctx(args)
    failures: list[str] = []
    strava = StravaClient(
        club_id=cfg.get("strava", "club_id"),
        club_url=cfg.get("strava", "club_url"),
    )
    print("[doctor] Strava 配置：", "已配置" if strava.is_configured() else "缺失")
    if strava.is_configured():
        authenticated = False
        try:
            strava.athlete_me()
            authenticated = True
            print("  [ok] 账号/refresh_token（1 条）")
        except Exception as e:
            failures.append("Strava/账号/refresh_token")
            print(f"  [fail] 账号/refresh_token：{_strava_failure_hint(e)}")

        if not authenticated:
            for name in (
                "Club 链接解析",
                "本人骑行统计",
                "私密 Club 活动",
                "本人活动/PR scope",
            ):
                print(f"  [skip] {name}（账号鉴权未通过）")
        else:
            checks = (
                ("Club 链接解析", strava.club_detail),
                ("本人骑行统计", strava.athlete_stats_material),
                ("私密 Club 活动", lambda: strava.club_activities(per_page=1)),
                ("本人活动/PR scope", lambda: strava.recent_activities(per_page=1)),
            )
            for name, check in checks:
                try:
                    value = check()
                    if name == "Club 链接解析" and not value:
                        raise RuntimeError("未配置或无法解析 Club ID/URL")
                    count = len(value) if isinstance(value, list) else 1
                    print(f"  [ok] {name}（{count} 条）")
                except Exception as e:
                    failures.append(f"Strava/{name}")
                    print(f"  [fail] {name}：{_strava_check_failure_hint(name, e)}")
    if llm.is_configured():
        try:
            llm.chat("你是连通性检查助手。", "只回复 OK。", temperature=0)
            print("[doctor] LLM：鉴权与模型调用正常")
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", "error")
            failures.append("LLM")
            print(f"[doctor] LLM：探活失败（HTTP {status}）")
    else:
        print("[doctor] LLM：未配置（只能离线骨架）")

    token_mgr = TokenManager(
        cfg.get("wechat", "token_cache", default="./data/.wechat_token.json")
    )
    if token_mgr.is_configured():
        try:
            token_mgr.get_token()
            print("[doctor] 微信公众号：鉴权正常")
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", "error")
            failures.append("微信公众号")
            print(f"[doctor] 微信公众号：鉴权失败（HTTP {status}）")
    else:
        print("[doctor] 微信公众号：未配置（不能推草稿箱）")

    if failures:
        raise RuntimeError(f"接入检查失败：{', '.join(failures)}")


def cmd_list(args) -> None:
    _cfg, queue, _assets, _llm = _ctx(args)
    rows = queue.list_all()
    if not rows:
        print("（暂无稿件）")
        return
    print(f"{'article_id':<16}{'state':<12}screens  title")
    for a in rows:
        print(f"{a.article_id:<16}{a.state.value:<12}{len(a.screens):<8}{a.title}")


def cmd_review(args) -> None:
    """查看或更新审核 checklist；序号从 1 开始。"""
    cfg, queue, _assets, _llm = _ctx(args)
    article = queue.get(args.article_id)
    if article.state != ArticleState.DRAFT:
        raise RuntimeError(f"当前状态 {article.state.value} 不允许审核，需先生成 draft")
    checklist = cfg.review_checklist
    queue.init_checklist(article, checklist)
    if args.check_all:
        article.review_checked = [True] * len(checklist)
    elif args.uncheck_all:
        article.review_checked = [False] * len(checklist)
    elif args.items:
        indexes = {int(x.strip()) - 1 for x in args.items.split(",") if x.strip()}
        invalid = [i + 1 for i in indexes if i < 0 or i >= len(checklist)]
        if invalid:
            raise ValueError(f"checklist 序号越界：{invalid}")
        for i in indexes:
            article.review_checked[i] = True
    queue.save(article)
    if article.gen_warnings:
        print("── 出稿自检提示（人工核对）──")
        for w in article.gen_warnings:
            print(f"  ⚠️  {w}")
        print("────────────────────────────────")
    for i, (text, checked) in enumerate(zip(checklist, article.review_checked), 1):
        print(f"[{'x' if checked else ' '}] {i}. {text}")
    print(f"进度：{sum(article.review_checked)}/{len(checklist)}")


def cmd_approve(args) -> None:
    cfg, queue, _assets, _llm = _ctx(args)
    article = queue.approve(args.article_id, cfg.review_checklist)
    print(f"[ok] 审核通过：{article.article_id} -> {article.state.value}")
    print("      下一步：python3 pipeline.py render", article.article_id)


def cmd_reject(args) -> None:
    _cfg, queue, _assets, _llm = _ctx(args)
    article = queue.reject(args.article_id, args.reason)
    print(
        f"[ok] 已打回：{article.article_id} -> {article.state.value}（补素材后重新 collect/generate）"
    )


def _preview_html(article_path: str | Path) -> str:
    """兼容旧调用的单页预览；真实 preview 服务使用 SSE，不再定时整页刷新。"""
    content = render_html(Article.load(article_path), include_review=True)
    return content.replace("<head>", '<head><meta http-equiv="refresh" content="2">', 1)


def cmd_preview(args) -> None:
    cfg, queue, _assets, llm = _ctx(args)
    article_path = queue.work_dir / f"{args.article_id}.json"
    if not article_path.is_file():
        raise RuntimeError(f"稿件不存在：{args.article_id}")
    renderer_style = cfg.get("renderer", "style", default="raw")
    handler = make_preview_handler(
        queue,
        args.article_id,
        list(cfg.review_checklist),
        llm,
        renderer_style=renderer_style,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"[ok] 实时预览：http://{args.host}:{server.server_port}（Ctrl-C 退出）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[ok] 预览已停止")
    finally:
        server.server_close()


def cmd_watch(args) -> None:
    """监听素材筐；只更新待处理素材清单，不覆盖 article.json。"""
    cfg, queue, _assets, llm = _ctx(args)
    article = queue.get(args.article_id)
    inbox_dir = cfg.path("inbox_dir", "./data/inbox")
    destination = queue.work_dir / args.article_id / "pending-materials.json"

    def report(payload: dict[str, object]) -> None:
        print(f"[watch] 发现 {payload['count']} 条新素材 -> {destination}")

    print(f"[ok] 正在监听 {inbox_dir}（Ctrl-C 退出）")
    try:
        watch_materials(
            inbox_dir,
            destination,
            article.materials,
            llm,
            args.interval,
            args.once,
            report,
        )
    except KeyboardInterrupt:
        print("\n[ok] 素材监听已停止")


def cmd_render(args) -> None:
    cfg, queue, _assets, _llm = _ctx(args)
    article = queue.get(args.article_id)
    style = cfg.get("renderer", "style", default="raw")
    png = render_long_image(article, queue.work_dir, style=style)
    queue.save(article)
    print(f"[ok] 长图已渲染：{png}（{article.state.value}）")


def cmd_ribao_audit(args) -> None:
    """Run release probes and print evidence suitable for final review."""
    if args.html and not args.assets_dir:
        raise ValueError("--html 模式必须同时提供 --assets-dir")
    report = audit_ribao(
        Path.cwd(),
        date=args.date,
        html_path=Path(args.html) if args.html else None,
        assets_dir=Path(args.assets_dir) if args.assets_dir else None,
        strict=args.strict,
    )
    payload = report.payload
    git = payload["git"]
    print(f"[git] {git['short_sha']} {git['subject']} | {'clean' if git['clean'] else 'DIRTY'}")
    if not git["clean"]:
        print("[warn] 工作区不干净；以下文件未纳入当前 HEAD 证据")
    for change in git["changes"]:
        print(f"      {change}")
    if payload["archived"]:
        print("[archive] 归档期豁免：" + ", ".join(payload["exemptions"]))
    for label, artifact in payload["artifacts"].items():
        if label == "html":
            continue
        width, height = artifact["size"]
        print(f"[artifact] {label}: {artifact['path']} | {width}×{height} | sha256={artifact['sha256']}")
    probes = payload["probes"]
    if probes:
        subject = probes["subject"]
        copy = probes["copy"]
        identity = probes["identity"]
        density = probes["density"]
        print(f"[probe] 人物：{subject['checked_images']} 图 / {subject['checked_points']} 点 / {len(subject['occlusions'])} 遮挡")
        print(f"[probe] 文字：{copy['copy_pixels']} 字素像素 / 贴纸 {copy['sticker_overlap_pixels']} / 图片 {copy['image_overlap_pixels']} / 任意层 {copy['overlap_pixels']} 命中")
        print(f"[probe] 唯一性：{identity['image_count']} 图 / {identity['identity_count']} 身份")
        print(f"[probe] 密度：总高 {density['total_height']}px")
        for section in density["sections"]:
            print(f"        第 {section['index']} 屏 {section['fill_ratio']:.2%}")
        for image in density["images"]:
            print(f"        第 {image['section']} 屏 {image['kind']} {Path(image['source']).name} {image['width']:.0f}×{image['height']:.0f}")
    print(f"[ok] 审计证据：{report.evidence_path}")


def _validate_push_export(article: Article, style: str = "card") -> None:
    """阻止正文或 renderer.style 变更后遗留的旧图片进入微信上传流程。"""
    current_hash = article_content_hash(article, style)
    if (
        article.render_style != style
        or not article.export_hash
        or article.export_hash != current_hash
    ):
        raise RuntimeError("当前长图与正文版本不一致，请重新 render 后再 push")
    if not article.image_path or not Path(article.image_path).is_file():
        raise RuntimeError("当前长图文件不存在，请重新 render 后再 push")


def cmd_push(args) -> None:
    cfg, queue, _assets, _llm = _ctx(args)
    article = queue.get(args.article_id)
    style = cfg.get("renderer", "style", default="raw")
    _validate_push_export(article, style)
    mgr = TokenManager(
        cfg.get("wechat", "token_cache", default="./data/.wechat_token.json")
    )
    media_id = push_draft(
        article,
        mgr,
        author=cfg.get("account", "author", default="辛庄桥小火车"),
        auto_publish=bool(cfg.get("wechat", "auto_publish", default=False)),
    )
    queue.save(article)
    print(f"[ok] 已进微信草稿箱 media_id={media_id}（{article.state.value}）")
    print("      个人主体：请在手机「公众号助手」预览并人工群发。")


def cmd_run(args) -> None:
    """一键：collect → generate；加 --auto 再 approve → render（→ --push 进草稿箱）。

    --auto 会把审核 checklist 全勾——真名/数据/本人同意这些红线就没人把关了，
    只建议自己先看长图、确认没问题再群发的用法；出稿自检的提示照样打印。
    """
    cmd_collect(args)
    cmd_generate(args)
    if not args.auto:
        return
    cfg, queue, _assets, _llm = _ctx(args)
    aid = _article_id(args.date, args.kind)
    article = queue.get(aid)
    queue.init_checklist(article, cfg.review_checklist)
    article.review_checked = [True] * len(cfg.review_checklist)
    queue.save(article)
    queue.approve(aid, cfg.review_checklist)
    article = queue.get(aid)
    png = render_long_image(
        article, queue.work_dir, style=cfg.get("renderer", "style", default="raw")
    )
    # render_long_image 就地写回路径、指纹和 IMAGE 状态；必须保存同一个实例，供紧接着的 --push 使用。
    queue.save(article)
    print(f"[ok] 长图：{png}")
    if article.gen_warnings:
        print("      ⚠️  出稿自检有提示，发之前看一眼：")
        for w in article.gen_warnings:
            print(f"         {w}")
    if args.push:
        args.article_id = aid
        cmd_push(args)
    else:
        print("      下一步：看图没问题 → python3 pipeline.py push", aid)


def cmd_setup(args) -> None:
    """安装运行依赖；主体安全能力缺失时最终以非零状态失败。"""
    import os
    import subprocess
    import sys

    mirror = os.getenv(
        "PLAYWRIGHT_DOWNLOAD_HOST", "https://npmmirror.com/mirrors/playwright"
    )
    steps = [
        (
            "chromium（渲染长图）",
            [sys.executable, "-m", "playwright", "install", "chromium-headless-shell"],
            {"PLAYWRIGHT_DOWNLOAD_HOST": mirror},
        ),
        (
            "梗图模板资源",
            [
                sys.executable,
                "-c",
                "import meme_generator as m; m.resources.check_resources()",
            ],
            {},
        ),
        (
            "抠图模型 u2net",
            [
                sys.executable,
                "-c",
                "from rembg import new_session; new_session('u2net')",
            ],
            {},
        ),
    ]
    for name, cmd, env in steps:
        print(f"[setup] {name} …", flush=True)
        try:
            subprocess.run(cmd, check=True, env={**os.environ, **env}, timeout=1800)
            print(f"        ✅ {name}")
        except Exception as e:
            print(
                f"        ⚠️  {name} 失败：{type(e).__name__}——不装也能出稿，只是没这一项"
            )
    print("[setup] 人脸框模型 yunet（字和贴纸躲开人脸）…", flush=True)
    try:
        from xzq.renderer.faces import MODEL, MODEL_URL

        if MODEL.exists():
            print("        ✅ 已有")
        else:
            import urllib.request

            MODEL.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(MODEL_URL, MODEL)
            print(f"        ✅ 下到 {MODEL}")
    except Exception as e:
        print(
            f"        ❌ 下载失败：{type(e).__name__}——人物主体安全门禁将阻断发布"
        )
    capability_errors = _subject_safety_capability_errors()
    if capability_errors:
        raise RuntimeError(
            "人物主体安全能力未就绪：" + "；".join(capability_errors)
        )
    for d in ("data/inbox", "data/articles"):
        Path(d).mkdir(parents=True, exist_ok=True)
    print(
        "[setup] 完成。接下来：cp .env.example .env 填 LLM_*，素材丢 data/inbox，python3 pipeline.py run --auto --date MMDD --kind ribao"
    )


def cmd_ribao_check(args) -> None:
    """一次执行日报发布前门禁。"""
    root = Path(__file__).resolve().parent
    if getattr(args, "html", None):
        if not args.assets_dir:
            raise ValueError("--html 模式必须同时提供 --assets-dir")
        report = validate_rendered_ribao(
            Path(args.html).expanduser(), Path(args.assets_dir).expanduser()
        )
    else:
        case_dir = Path(args.case_dir).expanduser() if args.case_dir else None
        report = validate_ribao_case(root=root, date=args.date, case_dir=case_dir)
    for check in report.checks:
        print(f"[pass] {check}")
    print("[ok] 日报发布前门禁全部通过")


def cmd_sticker_search(args) -> None:
    """从许可清晰的 OpenMoji 集合搜索可本地化贴图。"""
    catalog = NetworkStickerCatalog(args.style_dir)
    candidates = catalog.search_openmoji(args.query, limit=args.limit)
    if not candidates:
        print("（没有匹配的 OpenMoji；换英文近义词重试）")
        return
    for index, candidate in enumerate(candidates, 1):
        print(
            f"{index}. {candidate.identifier} | {candidate.title} | "
            f"{candidate.license}"
        )


def cmd_sticker_import(args) -> None:
    """下载选定开放贴图并登记到现有 stickers.json。"""
    catalog = NetworkStickerCatalog(args.style_dir)
    candidate = (
        catalog.openmoji(args.identifier)
        if args.provider == "openmoji"
        else catalog.twemoji(args.identifier)
    )
    path = catalog.import_candidate(candidate, name=args.name, use=args.use)
    print(f"[ok] 网络贴图已入库：{path}")
    print(
        f"     来源：{candidate.source_url} | {candidate.license} | "
        f"{candidate.attribution}"
    )


def _positive_finite_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("必须是有限正数") from error
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("必须是有限正数")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description="辛庄桥小火车 AI 推文流水线")
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--date", required=True, help="如 0913")
        p.add_argument(
            "--kind",
            default="ribao",
            choices=["ribao", "wanbao", "zaobao", "kuaixun", "subao", "tuanjian"],
        )

    p_run = sub.add_parser(
        "run", help="collect→generate；--auto 再审核+渲染；--push 再进草稿箱"
    )
    add_common(p_run)
    p_run.add_argument("--offline", action="store_true")
    p_run.add_argument(
        "--auto", action="store_true", help="跳过人工勾 checklist，直接渲染长图"
    )
    p_run.add_argument(
        "--push", action="store_true", help="配合 --auto，渲染完直接进微信草稿箱"
    )
    p_run.set_defaults(fn=cmd_run)
    sub.add_parser("setup", help="首次装 chromium / 梗图资源 / 抠图模型").set_defaults(
        fn=cmd_setup
    )
    p_col = sub.add_parser("collect")
    add_common(p_col)
    p_col.set_defaults(fn=cmd_collect)
    p_gen = sub.add_parser("generate")
    add_common(p_gen)
    p_gen.add_argument("--offline", action="store_true")
    p_gen.set_defaults(fn=cmd_generate)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p_rv = sub.add_parser("review")
    p_rv.add_argument("article_id")
    rv = p_rv.add_mutually_exclusive_group()
    rv.add_argument("--check-all", action="store_true", help="人工核对后全部勾选")
    rv.add_argument("--uncheck-all", action="store_true")
    rv.add_argument("--items", default="", help="勾选序号，如 1,3,5")
    p_rv.set_defaults(fn=cmd_review)
    p_ap = sub.add_parser("approve")
    p_ap.add_argument("article_id")
    p_ap.set_defaults(fn=cmd_approve)
    p_rj = sub.add_parser("reject")
    p_rj.add_argument("article_id")
    p_rj.add_argument("--reason", default="")
    p_rj.set_defaults(fn=cmd_reject)
    p_rd = sub.add_parser("render")
    p_rd.add_argument("article_id")
    p_rd.set_defaults(fn=cmd_render)
    p_preview = sub.add_parser("preview", help="实时预览稿件，源 JSON 变化后自动刷新")
    p_preview.add_argument("article_id")
    p_preview.add_argument("--host", default="127.0.0.1")
    p_preview.add_argument("--port", type=int, default=8000)
    p_preview.set_defaults(fn=cmd_preview)
    p_watch = sub.add_parser("watch", help="监听素材筐，只生成待处理清单")
    p_watch.add_argument("article_id")
    p_watch.add_argument("--interval", type=_positive_finite_float, default=1.0)
    p_watch.add_argument(
        "--once", action="store_true", help="扫描一次后退出（便于自动化）"
    )
    p_watch.set_defaults(fn=cmd_watch)
    p_ps = sub.add_parser("push")
    p_ps.add_argument("article_id")
    p_ps.set_defaults(fn=cmd_push)
    p_gate = sub.add_parser("ribao-check", help="检查日报发布前全部静态产物")
    gate_mode = p_gate.add_mutually_exclusive_group(required=True)
    gate_mode.add_argument("--date", help="YYYYMMDD，例如 20260908")
    gate_mode.add_argument("--html", help="build 中待检查的合并 HTML")
    p_gate.add_argument("--assets-dir", default="", help="--html 模式的产物目录")
    p_gate.add_argument(
        "--case-dir", default="", help="可选：覆盖默认 docs/ribao-html/MMDD"
    )
    p_gate.set_defaults(fn=cmd_ribao_check)
    p_audit = sub.add_parser("ribao-audit", help="只读复跑日报门禁并生成终审证据 JSON")
    mode = p_audit.add_mutually_exclusive_group(required=True)
    mode.add_argument("--date", help="YYYYMMDD，审计 docs/ribao-html/MMDD")
    mode.add_argument("--html", help="build 中待审计的合并 HTML")
    p_audit.add_argument("--assets-dir", default="", help="--html 模式的长图/封面所在目录")
    p_audit.add_argument("--strict", action="store_true", help="工作区非 clean 时失败")
    p_audit.set_defaults(fn=cmd_ribao_audit)
    p_sticker_search = sub.add_parser(
        "sticker-search", help="搜索可本地化的 OpenMoji 网络贴图"
    )
    p_sticker_search.add_argument("query", help="建议使用英文关键词，如 bicycle")
    p_sticker_search.add_argument("--limit", type=int, default=8)
    p_sticker_search.add_argument("--style-dir", default="style")
    p_sticker_search.set_defaults(fn=cmd_sticker_search)
    p_sticker_import = sub.add_parser(
        "sticker-import", help="把 OpenMoji/Twemoji 下载并登记到贴纸库"
    )
    p_sticker_import.add_argument("identifier", help="OpenMoji ID 或单个 emoji")
    p_sticker_import.add_argument(
        "--provider", required=True, choices=["openmoji", "twemoji"]
    )
    p_sticker_import.add_argument("--name", required=True, help="贴纸中文名")
    p_sticker_import.add_argument("--use", required=True, help="用途关键词，以 / 分隔")
    p_sticker_import.add_argument("--style-dir", default="style")
    p_sticker_import.set_defaults(fn=cmd_sticker_import)

    args = parser.parse_args()
    try:
        args.fn(args)
    except (RuntimeError, ValueError, OSError) as error:
        print(f"[error] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
