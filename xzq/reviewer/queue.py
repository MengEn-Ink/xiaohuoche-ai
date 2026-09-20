"""稿件队列：在 work_dir 里按 article_id 落盘 JSON，支持列出、审核通过、打回。

审核是强制人工环节：必须 checklist 全勾才允许进入渲染（见 models.Article.approve）。
"""
from __future__ import annotations

from pathlib import Path

from ..models import Article, ArticleState, IllegalTransition


class ArticleQueue:
    def __init__(self, work_dir: str | Path) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, article_id: str) -> Path:
        return self.work_dir / f"{article_id}.json"

    def save(self, article: Article) -> Path:
        return article.save(self._path(article.article_id))

    def get(self, article_id: str) -> Article:
        return Article.load(self._path(article_id))

    def exists(self, article_id: str) -> bool:
        return self._path(article_id).exists()

    def list_all(self) -> list[Article]:
        return [
            Article.load(p)
            for p in sorted(self.work_dir.glob("*.json"))
            if not p.name.startswith(".")
        ]

    def list_by_state(self, state: ArticleState) -> list[Article]:
        return [a for a in self.list_all() if a.state == state]

    def init_checklist(self, article: Article, checklist: list[str]) -> Article:
        """按检查项文本同步勾选状态；新增、改名或重排不会误继承。"""
        previous = {
            item: checked
            for item, checked in zip(article.review_items, article.review_checked)
        }
        article.review_items = list(checklist)
        article.review_checked = [previous.get(item, False) for item in checklist]
        return article

    def approve(self, article_id: str, checklist: list[str]) -> Article:
        """人工确认所有 checklist 项后调用；未全勾会抛 IllegalTransition。"""
        article = self.get(article_id)
        self.init_checklist(article, checklist)
        article.approve(len(checklist))
        self.save(article)
        return article

    def reject(self, article_id: str, reason: str = "") -> Article:
        """打回补素材/重写：draft/approved -> inbox。reason 记入末屏 data 供追溯。"""
        article = self.get(article_id)
        if article.state not in (ArticleState.DRAFT, ArticleState.APPROVED):
            raise IllegalTransition(f"当前状态 {article.state.value} 不允许打回")
        # approved 先回 draft 再回 inbox；draft 直接回 inbox
        if article.state == ArticleState.APPROVED:
            article.transition(ArticleState.DRAFT)
        if reason and article.screens:
            article.screens[-1].data["reject_reason"] = reason
        article.transition(ArticleState.INBOX)
        self.save(article)
        return article
