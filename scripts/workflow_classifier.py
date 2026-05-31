"""workflow_classifier — автоматическая классификация запросов пользователя.

Определяет тип задачи (workflow) по тексту запроса через regex-правила.
Возвращает WorkflowProfile с именем workflow, skill, toolsets и confidence.
"""
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WorkflowProfile:
    name: str
    skill: Optional[str] = None
    toolsets: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class _Rule:
    pattern: str
    workflow: str
    weight: float
    compiled: re.Pattern


class WorkflowClassifier:
    """Classifies user requests into workflow profiles using regex rules."""

    def __init__(self):
        self.rules: list[_Rule] = []
        self._workflow_meta: dict[str, tuple[Optional[str], list[str]]] = {}

    def register_workflow(self, name: str, skill: Optional[str] = None, toolsets: Optional[list[str]] = None) -> None:
        """Register workflow metadata (skill and toolsets)."""
        self._workflow_meta[name] = (skill, toolsets or [])

    def add_rule(self, pattern: str, workflow: str, weight: float = 1.0) -> None:
        """Add a regex rule for a workflow."""
        self.rules.append(
            _Rule(pattern=pattern, workflow=workflow, weight=weight,
                  compiled=re.compile(pattern, re.IGNORECASE))
        )

    def add_keywords(self, workflow: str, keywords: list[str], weight: float = 0.7) -> None:
        """Add keyword-based rules (each word becomes a \\bword\\b regex)."""
        for word in keywords:
            pattern = r'\b' + re.escape(word) + r'\b'
            self.rules.append(
                _Rule(pattern=pattern, workflow=workflow, weight=weight,
                      compiled=re.compile(pattern, re.IGNORECASE))
            )

    def _score_workflows(self, text: str) -> dict[str, float]:
        """Score all workflows based on matching rules."""
        scores: dict[str, float] = {}
        for rule in self.rules:
            if rule.compiled.search(text):
                scores[rule.workflow] = scores.get(rule.workflow, 0.0) + rule.weight
        # Convert sum to confidence: min(1.0, sum / 2.0)
        return {wf: min(1.0, total / 2.0) for wf, total in scores.items()}

    def classify(self, text: str) -> Optional[WorkflowProfile]:
        """Return the workflow with the highest confidence, or None if confidence == 0."""
        scores = self._score_workflows(text)
        if not scores:
            return None
        max_confidence = max(scores.values())
        if max_confidence == 0.0:
            return None
        # Find workflow with highest confidence (tie-break by first encountered)
        best_wf = max(scores, key=lambda wf: (scores[wf], -self._workflow_index(wf)))
        skill, toolsets = self._workflow_meta.get(best_wf, (None, []))
        return WorkflowProfile(
            name=best_wf,
            skill=skill,
            toolsets=toolsets,
            confidence=max_confidence
        )

    def _workflow_index(self, workflow_name: str) -> int:
        """Return the first rule index for a workflow (for stable tie-breaking)."""
        for i, rule in enumerate(self.rules):
            if rule.workflow == workflow_name:
                return i
        return len(self.rules)

    def classify_all(self, text: str) -> list[WorkflowProfile]:
        """Return all matching workflows sorted by confidence descending."""
        scores = self._score_workflows(text)
        sorted_wfs = sorted(scores.items(), key=lambda x: -x[1])
        result = []
        for wf_name, confidence in sorted_wfs:
            if confidence > 0.0:
                skill, toolsets = self._workflow_meta.get(wf_name, (None, []))
                result.append(WorkflowProfile(name=wf_name, skill=skill, toolsets=toolsets, confidence=confidence))
        return result

    def register_defaults(self) -> None:
        """Register built-in workflow rules and metadata."""
        # Workflow metadata
        self.register_workflow("ford_diagnostics", skill="auto-diagnostics", toolsets=["terminal", "file", "web"])
        self.register_workflow("article_writing", skill="autolycus-article-writer", toolsets=["web", "file"])
        self.register_workflow("outcome_contract", skill="bitgn", toolsets=["terminal", "file"])
        self.register_workflow("bitgn_research", skill="bitgn", toolsets=["web", "file"])
        self.register_workflow("email_security", toolsets=["terminal", "file"])
        self.register_workflow("diagnostic_generic", toolsets=["terminal", "file", "web"])

        # Keywords rules
        self.add_keywords("ford_diagnostics", [
            "ford", "explorer",
            "форд", "эксплорер", "форд эксплорер",
            "gem модуль", "5r55e", "акпп",
            "двигатель 4.0", "check engine",
        ], weight=0.7)
        self.add_keywords("article_writing", [
            "статья", "статью", "статьи",
            "telegra.ph", "пост", "публикация", "черновик", "draft", "habr",
            "напиши",
        ], weight=0.7)
        self.add_keywords("outcome_contract", [
            "outcome contract", "task_outcome", "verify gate", "trust boundaries",
        ], weight=0.7)
        self.add_keywords("bitgn_research", [
            "bitgn", "ecom1", "ecom2", "pac1", "agent challenge", "агентные соревнования",
        ], weight=0.7)
        self.add_keywords("email_security", [
            "email", "mail", "письмо", "почта", "phishing", "spam", "проверь почту",
        ], weight=0.7)
        self.add_keywords("diagnostic_generic", [
            "диагностика", "проблема", "не работает", "ошибка", "сломалось", "глючит",
        ], weight=0.7)