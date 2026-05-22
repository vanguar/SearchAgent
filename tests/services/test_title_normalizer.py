from app.services.title_normalizer import TitleNormalizer


def test_title_normalizer_removes_gender_markers_and_noise() -> None:
    normalizer = TitleNormalizer()

    normalized = normalizer.normalize("Lagermitarbeiter/in (m/w/d) - Vollzeit")

    assert normalized == "lagermitarbeiter"


def test_title_normalizer_keeps_role_meaning_for_helper_titles() -> None:
    normalizer = TitleNormalizer()

    normalized = normalizer.normalize("Produktionshelfer*in | ab sofort")

    assert normalized == "produktionshelfer"
