"""Tests for workflow_classifier module."""
from scripts.workflow_classifier import WorkflowClassifier


def test_default_rules_prepopulated():
    """register_defaults populates the rules list."""
    clf = WorkflowClassifier()
    clf.register_defaults()
    assert len(clf.rules) > 0


def test_classify_ford_explicit():
    """"проверь форд эксплорер" → name="ford_diagnostics", confidence >= 0.5."""
    clf = WorkflowClassifier()
    clf.register_defaults()
    result = clf.classify("проверь форд эксплорер")
    assert result is not None
    assert result.name == "ford_diagnostics"
    assert result.confidence >= 0.5


def test_classify_ford_gem():
    """"gem модуль проблемы" → "ford_diagnostics"."""
    clf = WorkflowClassifier()
    clf.register_defaults()
    result = clf.classify("gem модуль проблемы")
    assert result is not None
    assert result.name == "ford_diagnostics"


def test_classify_article():
    """"напиши статью про Prism" → "article_writing"."""
    clf = WorkflowClassifier()
    clf.register_defaults()
    result = clf.classify("напиши статью про Prism")
    assert result is not None
    assert result.name == "article_writing"


def test_classify_bitgn():
    """"изучи bitgn ecom1" → "bitgn_research"."""
    clf = WorkflowClassifier()
    clf.register_defaults()
    result = clf.classify("изучи bitgn ecom1")
    assert result is not None
    assert result.name == "bitgn_research"


def test_classify_email():
    """"проверь почту" → "email_security"."""
    clf = WorkflowClassifier()
    clf.register_defaults()
    result = clf.classify("проверь почту")
    assert result is not None
    assert result.name == "email_security"


def test_classify_diagnostic_generic():
    """"не работает двигатель" → "diagnostic_generic"."""
    clf = WorkflowClassifier()
    clf.register_defaults()
    result = clf.classify("не работает двигатель")
    assert result is not None
    assert result.name == "diagnostic_generic"


def test_classify_unknown():
    """"как дела?" → None (confidence 0)."""
    clf = WorkflowClassifier()
    clf.register_defaults()
    result = clf.classify("как дела?")
    assert result is None


def test_classify_prefers_highest_confidence():
    """Текст совпадающий с двумя workflows → возвращает тот что с higher confidence."""
    clf = WorkflowClassifier()
    clf.register_defaults()
    # "почта" triggers email_security, "не работает" triggers diagnostic_generic
    # Text that matches both: "не работает почта"
    result = clf.classify("не работает почта")
    assert result is not None
    # Both should match, but we just verify it picks one with reasonable confidence
    assert result.confidence > 0.0
    # email: "проверь почту" = 1 keyword, "письмо" = 1 keyword → 1.4/2 = 0.7
    # diagnostic_generic: "не работает" = 0.7/2 = 0.35
    result = clf.classify("проверь почту письмо не работает")
    assert result is not None
    assert result.name == "email_security"
    assert result.confidence == 0.7  # 2 keywords: "проверь почту" + "письмо"


def test_classify_all_returns_sorted():
    """classify_all возвращает все совпадения, отсортированные по confidence desc."""
    clf = WorkflowClassifier()
    clf.register_defaults()
    # "не работает почта" should match diagnostic_generic and email_security
    results = clf.classify_all("не работает почта")
    assert len(results) >= 2
    confidences = [r.confidence for r in results]
    assert confidences == sorted(confidences, reverse=True)


def test_register_custom_rule():
    """add_rule с новым workflow, classify находит его."""
    clf = WorkflowClassifier()
    clf.add_rule(r"\bweather\b", "weather_check", weight=1.0)
    result = clf.classify("what is the weather today")
    assert result is not None
    assert result.name == "weather_check"
    assert result.confidence == 0.5  # 1.0 / 2.0


def test_classify_case_insensitive():
    """"FORD EXPLORER" → "ford_diagnostics"."""
    clf = WorkflowClassifier()
    clf.register_defaults()
    result = clf.classify("FORD EXPLORER")
    assert result is not None
    assert result.name == "ford_diagnostics"