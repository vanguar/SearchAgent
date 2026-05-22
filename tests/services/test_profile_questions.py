from app.services.profile_questions import ProfileQuestionGenerator


def test_question_generator_returns_only_missing_field_questions() -> None:
    generator = ProfileQuestionGenerator()

    questions = generator.generate(("desired_roles", "german_level", "shift_ok"))

    assert len(questions) == 3
    assert questions[0].field_name == "desired_roles"
    assert questions[1].field_name == "german_level"
    assert questions[2].field_name == "shift_ok"
    assert all(question.label for question in questions)
