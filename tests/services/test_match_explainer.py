from app.services.match_explainer import MatchExplainer
from app.services.search_models import FilterResult, RuleHit, ScoreResult


def test_match_explainer_formats_hot_match_in_short_russian() -> None:
    explainer = MatchExplainer()
    explanation = explainer.explain(
        filter_result=FilterResult(
            decision="allow",
            positive_hits=(
                RuleHit(code="warehouse_family", label_ru="складская роль"),
                RuleHit(code="shift_fit", label_ru="смены допустимы"),
            ),
        ),
        score_result=ScoreResult(
            score=86,
            positive_hits=(RuleHit(code="low_language_signal", label_ru="низкий языковой барьер", weight=12),),
        ),
    )

    assert explanation == "Подходит: складская роль, смены допустимы, низкий языковой барьер."


def test_match_explainer_formats_review_case_in_short_russian() -> None:
    explainer = MatchExplainer()
    explanation = explainer.explain(
        filter_result=FilterResult(
            decision="review",
            positive_hits=(RuleHit(code="warehouse_family", label_ru="складская роль"),),
            review_hits=(RuleHit(code="sponsorship_review", label_ru="есть вопросы по допуску к работе"),),
        ),
        score_result=ScoreResult(score=62),
    )

    assert explanation == "С осторожностью: складская роль, есть вопросы по допуску к работе."


def test_match_explainer_medium_band_uses_adjacent_prefix() -> None:
    explainer = MatchExplainer()
    explanation = explainer.explain(
        filter_result=FilterResult(
            decision="allow",
            positive_hits=(RuleHit(code="production_family", label_ru="производственная роль"),),
        ),
        score_result=ScoreResult(score=72),
        relevance_band="medium",
    )

    assert explanation == "Смежная роль: производственная роль."


def test_match_explainer_high_band_uses_standard_prefix() -> None:
    explainer = MatchExplainer()
    explanation = explainer.explain(
        filter_result=FilterResult(
            decision="allow",
            positive_hits=(RuleHit(code="warehouse_family", label_ru="складская роль"),),
        ),
        score_result=ScoreResult(score=80),
        relevance_band="high",
    )

    assert explanation == "Подходит: складская роль."


def test_match_explainer_default_band_is_high() -> None:
    """Omitting relevance_band defaults to 'high', preserving backward compatibility."""
    explainer = MatchExplainer()
    explanation = explainer.explain(
        filter_result=FilterResult(
            decision="allow",
            positive_hits=(RuleHit(code="warehouse_family", label_ru="складская роль"),),
        ),
        score_result=ScoreResult(score=80),
    )

    assert explanation.startswith("Подходит:")
