"""把独立审阅资产内联进自包含预览页。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ASSET_DIR = Path(__file__).with_name("assets")


def review_fragments(config: dict[str, Any]) -> tuple[str, str, str]:
    css = (ASSET_DIR / "review.css").read_text(encoding="utf-8")
    javascript = (ASSET_DIR / "review.js").read_text(encoding="utf-8")
    encoded = json.dumps(config, ensure_ascii=False).replace("</", "<\\/")
    head = f'<style data-export-ignore="review-style">{css}</style>'
    body = """<div class="review-topbar" id="reviewTopbar" data-export-ignore="review-ui">
  <button class="review-topbar-action" id="reviewBack" type="button" aria-label="返回上一页">←</button>
  <b class="review-title" id="reviewTitle">日报审阅</b>
  <span class="review-sync-state" role="status" aria-live="polite"><span class="review-sync-dot" aria-hidden="true"></span><b id="reviewStatus" data-state="syncing">正在连接服务</b></span>
  <span class="review-version" id="reviewVersion"></span><button class="review-topbar-action" id="showNewVersion" type="button" hidden>新版本</button>
  <button class="review-topbar-action" id="reviewMore" type="button" aria-label="更多审阅操作">⋯</button>
</div>
<aside class="review-shell" id="reviewShell" data-export-ignore="review-ui" data-snap="peek" data-view="opinions" aria-label="审阅工具" aria-hidden="false">
  <button class="review-drag-handle" id="reviewDragHandle" type="button" aria-label="拖动审阅抽屉；点击切换半屏和全屏"><span></span></button>
  <header class="review-toolbar">
    <div class="review-state"><span>审阅 · <b id="reviewCount">0 条待处理</b></span><button class="review-icon-button" id="closeReviewDrawer" type="button" aria-label="收起审阅抽屉">⌄ 收起</button></div>
    <div class="review-main-actions">
      <button class="review-button" id="addAnnotation" type="button" aria-pressed="false">＋ 批注模式</button>
      <button class="review-button" id="generateRevision" data-online-action type="button">↻ 生成修订</button>
      <button class="review-button secondary" type="button" data-view="checklist">✓ 发布检查</button><button class="review-button secondary" type="button" data-view="submit">↑ 提交</button>
    </div>
  </header>
  <div class="review-gate" id="reviewGate">正在检查门禁</div>
  <div class="review-offline-actions" id="reviewOfflineActions" hidden><span>离线修改已暂存</span><button id="retrySync" type="button">重试同步</button></div>
  <section class="review-view review-opinions-view" data-review-view="opinions" aria-label="批注意见">
    <div class="review-filters" role="group" aria-label="筛选意见"><button class="review-filter active" data-filter="open">待处理</button><button class="review-filter" data-filter="resolved">已解决</button><button class="review-filter" data-filter="all">全部</button></div>
    <div class="review-list" id="reviewList" aria-label="批注意见列表"></div>
  </section>
  <section class="review-view review-checklist" id="reviewChecklist" data-review-view="checklist" aria-labelledby="reviewChecklistTitle" hidden>
    <div class="review-section-heading"><b id="reviewChecklistTitle">发布检查</b><span id="reviewChecklistProgress">0/0</span></div>
    <p class="review-check-reason" id="reviewCheckReason" hidden>正文已修订，原检查结果失效，请重新逐项确认。</p>
    <p id="reviewChecklistEmpty">当前没有检查项</p><div id="reviewChecklistPending"></div>
    <details id="reviewChecklistCompleted"><summary>已完成 <span id="reviewChecklistCompletedCount">0</span></summary><div id="reviewChecklistDone"></div></details>
  </section>
  <section class="review-view review-submit-view" data-review-view="submit" hidden aria-labelledby="reviewSubmitTitle">
    <h2 id="reviewSubmitTitle">提交审批</h2><div class="review-approval-summary" id="approvalSummary"></div>
    <p class="review-confirm-copy">确认正文、批注和发布检查均已复核。批准后才能生成发布图片。</p>
    <button class="review-button" id="approveReview" data-online-action type="button">查看审批总结</button>
  </section>
  <section class="review-view review-more-view" data-review-view="more" hidden aria-label="更多操作">
    <div class="review-more-actions">
      <button class="review-button secondary" id="requestChanges" type="button">继续修改</button><button class="review-button secondary" id="toggleGeneralNote" type="button">整页说明</button>
      <button class="review-button secondary" id="exportReview" type="button">导出审阅包</button><label class="review-button secondary" for="importReview">导入审阅包</label><input id="importReview" type="file" accept=".json,application/json" hidden>
      <button class="review-button" id="downloadProduct" data-online-action type="button">生成成品</button>
    </div>
  </section>
  <div class="review-diff" id="reviewDiff" hidden><b id="reviewDiffSummary"></b><div class="review-card-actions"><button id="acceptRevision" type="button">接受修改</button><button id="continueRevision" type="button">继续调整</button><button id="restoreRevision" type="button">保留原稿</button></div></div>
  <div class="review-note" id="reviewNote" hidden><label for="reviewGeneralNote"><b>整页说明</b></label><textarea id="reviewGeneralNote" placeholder="可使用系统键盘语音输入；不会保存音频"></textarea></div>
  <div class="review-composer" id="reviewComposer" hidden><b id="reviewComposerLabel">添加批注</b><div class="review-quick-tags" id="reviewQuickTags" aria-label="快捷批注标签"></div><div class="review-recent-notes" id="reviewRecentNotes" aria-label="最近使用意见" hidden></div><textarea id="reviewText" placeholder="写下这里需要怎么修改；可使用系统语音输入" enterkeyhint="done"></textarea><div class="review-card-actions"><button id="cancelAnnotation" type="button">取消</button><button id="saveAnnotation" type="button">保存批注</button></div></div>
  <div class="review-conflicts" id="reviewConflicts" hidden role="alert"><h2>批注冲突</h2><p>请选择保留我的版本或远端版本。</p><div id="reviewConflictList"></div></div>
</aside>
<nav class="review-bottom-bar" id="reviewBottomBar" data-export-ignore="review-ui" aria-label="移动审阅操作">
  <button id="mobileAnnotate" type="button" data-action="annotate"><span aria-hidden="true">＋</span>批注</button><button id="mobileReviewToggle" type="button" data-view="opinions"><span aria-hidden="true">◉</span>意见 <b id="mobileOpinionCount">0</b></button><button id="mobileChecklist" type="button" data-view="checklist"><span aria-hidden="true">✓</span>检查 <b id="mobileChecklistProgress">0/0</b></button><button id="mobileSubmit" type="button" data-view="submit"><span aria-hidden="true">↑</span>提交</button>
</nav>
<div class="review-undo" id="reviewUndo" data-export-ignore="review-ui" hidden role="status">批注已删除 <button id="undoDelete" type="button">撤销</button></div>
<div class="review-approval-dialog" id="approvalDialog" data-export-ignore="review-ui" hidden role="dialog" aria-modal="true" aria-labelledby="approvalDialogTitle"><div><h2 id="approvalDialogTitle">确认批准发布</h2><div id="approvalDialogSummary"></div><p>这是一次显式批准操作，确认所有检查均已完成。</p><div class="review-card-actions"><button id="cancelApproval" type="button">返回检查</button><button class="review-button" id="confirmApproval" data-online-action type="button">确认批准</button></div></div></div>"""
    script = f"<script>window.XZQ_REVIEW={encoded};</script><script>{javascript}</script>"
    return head, body, script
