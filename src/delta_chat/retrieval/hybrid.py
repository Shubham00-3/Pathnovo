"""Hybrid retrieval: exact tags + TF-IDF word/char + RRF."""

from __future__ import annotations

import re
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from delta_chat.retrieval.records import RetrievalRecord

TAG_RE = re.compile(r"\b\d{0,3}-?[A-Z]{1,6}-?\d{2,5}\b", re.I)


def route_query(question: str) -> dict[str, Any]:
    q = question.lower()
    families: list[str] = ["rev_a", "rev_b", "delta"]
    intent = "general"
    if any(
        w in q for w in ("change", "delta", "added", "removed", "difference", "modified", "moved")
    ):
        intent = "delta"
        families = ["delta", "rev_a", "rev_b"]
    if any(w in q for w in ("before", "old", "rev a", "revision a", "pid a")):
        intent = "rev_a"
        families = ["rev_a", "delta"]
    if any(w in q for w in ("current", "new", "rev b", "revision b", "pid b", "after")):
        intent = "rev_b"
        families = ["rev_b", "delta"]
    if "high-confidence" in q or "high confidence" in q:
        intent = "delta_high"
        families = ["delta"]
    return {"intent": intent, "families": families}


def _rrf(rank_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for r, sid in enumerate(ranks):
            scores[sid] = scores.get(sid, 0.0) + 1.0 / (k + r + 1)
    return scores


class HybridRetriever:
    def __init__(self, records: list[RetrievalRecord], config: dict | None = None) -> None:
        self.records = records
        self.by_id = {r.source_id: r for r in records}
        self.config = config or {}
        self.texts = [f"{r.text} {' '.join(r.identifiers)}" for r in records]
        self.word_vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
        self.char_vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1)
        if self.texts:
            self.word_mat = self.word_vec.fit_transform(self.texts)
            self.char_mat = self.char_vec.fit_transform(self.texts)
        else:
            self.word_mat = None
            self.char_mat = None

    def search(self, question: str, top_k: int | None = None) -> list[dict[str, Any]]:
        top_k = top_k or int(self.config.get("retrieval", {}).get("top_k", 8))
        rrf_k = int(self.config.get("retrieval", {}).get("rrf_k", 60))
        routing = route_query(question)
        allowed = set(routing["families"])
        candidates = [r for r in self.records if r.source_family in allowed] or self.records
        if not candidates:
            return []

        # exact identifier boost
        tags = [m.group(0).upper().replace(" ", "") for m in TAG_RE.finditer(question)]
        # also bare tag fragments like 26-PIT-9062 already covered; add PIT-9062 style
        for m in re.finditer(r"\b[A-Z]{1,6}-\d{2,5}\b", question, re.I):
            tags.append(m.group(0).upper())
        exact_hits: list[str] = []
        for r in candidates:
            idents = [i.upper().replace(" ", "") for i in r.identifiers]
            blob = (r.text or "").upper().replace(" ", "")
            for t in tags:
                t_norm = t.replace(" ", "").upper()
                if (
                    t_norm in idents
                    or t_norm in blob
                    or t_norm.replace("-", "") in blob.replace("-", "")
                ):
                    if r.source_id not in exact_hits:
                        exact_hits.append(r.source_id)
            if any(t.lower() in (r.text or "").lower() for t in tags):
                if r.source_id not in exact_hits:
                    exact_hits.append(r.source_id)

        # TF-IDF over full corpus then filter
        word_ranks: list[str] = []
        char_ranks: list[str] = []
        if self.word_mat is not None and self.texts:
            q_word = self.word_vec.transform([question])
            sims = cosine_similarity(q_word, self.word_mat).ravel()
            order = sims.argsort()[::-1]
            for idx in order:
                rid = self.records[idx].source_id
                if self.records[idx].source_family in allowed or not allowed:
                    word_ranks.append(rid)
                if len(word_ranks) >= top_k * 3:
                    break
            q_char = self.char_vec.transform([question])
            sims_c = cosine_similarity(q_char, self.char_mat).ravel()
            order_c = sims_c.argsort()[::-1]
            for idx in order_c:
                rid = self.records[idx].source_id
                if self.records[idx].source_family in allowed or not allowed:
                    char_ranks.append(rid)
                if len(char_ranks) >= top_k * 3:
                    break

        scores = _rrf([exact_hits, word_ranks, char_ranks], k=rrf_k)
        # boost exact
        for sid in exact_hits:
            scores[sid] = scores.get(sid, 0) + 0.5

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        out = []
        for sid, sc in ranked[:top_k]:
            rec = self.by_id.get(sid)
            if not rec:
                continue
            out.append(
                {
                    "source_id": sid,
                    "score": round(float(sc), 5),
                    "record": rec.model_dump(),
                    "routing": routing,
                }
            )
        return out
