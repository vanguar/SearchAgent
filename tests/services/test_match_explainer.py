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


def test_medium_band_with_title_role_is_not_called_adjacent() -> None:
    explainer = MatchExplainer()
    explanation = explainer.explain(
        filter_result=FilterResult(
            decision="allow",
            positive_hits=(RuleHit(code="light_goods_transport_family", label_ru="перевозка грузов на лёгком автомобиле"),),
        ),
        score_result=ScoreResult(
            score=69,
            positive_hits=(RuleHit(code="priority_role", label_ru="перевозка грузов на лёгком автомобиле", weight=26),),
            negative_hits=(RuleHit(code="commute_distance", label_ru="112 км от дома — реально только с переездом", weight=-8),),
        ),
        relevance_band="medium",
    )

    assert explanation == (
        "Роль совпадает: перевозка грузов на лёгком автомобиле; минус: 112 км от дома — реально только с переездом."
    )


def test_medium_band_with_title_role_and_no_minus_says_data_is_thin() -> None:
    explainer = MatchExplainer()
    explanation = explainer.explain(
        filter_result=FilterResult(decision="allow"),
        score_result=ScoreResult(
            score=60,
            positive_hits=(RuleHit(code="priority_role", label_ru="перевозка грузов на лёгком автомобиле", weight=26),),
        ),
        relevance_band="medium",
    )

    assert explanation == "Роль совпадает: перевозка грузов на лёгком автомобиле; других плюсов в объявлении не указано."
