from __future__ import annotations

from app.services.intake_models import FollowUpQuestion

QUESTION_MAP: dict[str, FollowUpQuestion] = {
    "desired_roles": FollowUpQuestion(
        field_name="desired_roles",
        label="Какие роли вам подходят в первую очередь? (через запятую)",
        input_type="text",
        placeholder="Python Developer, Backend Developer или другая ваша роль",
    ),
    "preferred_regions": FollowUpQuestion(
        field_name="preferred_regions",
        label="Где хотите искать работу? (город или регион)",
        input_type="text",
        placeholder="Берлин или Вся Германия",
    ),
    "willing_to_relocate": FollowUpQuestion(
        field_name="willing_to_relocate",
        label="Готовы к переезду?",
        input_type="yes_no",
    ),
    "german_level": FollowUpQuestion(
        field_name="german_level",
        label="Какой у вас уровень немецкого?",
        input_type="text",
        placeholder="none / basic / intermediate / advanced",
    ),
    "shift_ok": FollowUpQuestion(
        field_name="shift_ok",
        label="Сменный график подходит?",
        input_type="yes_no",
    ),
    "work_authorized": FollowUpQuestion(
        field_name="work_authorized",
        label="Есть разрешение на работу в Германии?",
        input_type="yes_no",
    ),
}


class ProfileQuestionGenerator:
    """Generate short Russian follow-up questions for missing critical fields."""

    def generate(self, missing_fields: tuple[str, ...]) -> tuple[FollowUpQuestion, ...]:
        questions: list[FollowUpQuestion] = []
        for field_name in missing_fields:
            question = QUESTION_MAP.get(field_name)
            if question is not None:
                questions.append(question)
        return tuple(questions)
