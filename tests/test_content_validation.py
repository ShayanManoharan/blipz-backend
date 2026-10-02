from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.content_validation import (
    CONTENT_SCHEMA_VERSION,
    GENERATOR_VERSION,
    PackageValidationError,
    validate_daily_package,
    validate_playable_package,
)


REVISION = "b47d60cc-5750-42d4-bb44-07dc1cd0c86c"


def _valid_package() -> dict:
    storage_key = f"daily/2026-08-15/{REVISION}.png"
    return {
        "content_schema_version": CONTENT_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "package_revision_id": REVISION,
        "original_image_prompt": "A cat sailing a teacup across a pond",
        "effective_image_prompt": None,
        "image_prompt": "A cat sailing a teacup across a pond",
        "image_storage_key": storage_key,
        "image_url": f"https://example.supabase.co/storage/v1/object/public/blipz-images/{storage_key}",
        "image_sha256": "a" * 64,
        "image_model": "gpt-image-1",
        "image_generated_at": datetime.now(timezone.utc).isoformat(),
        "image_verified_at": datetime.now(timezone.utc).isoformat(),
        "math_problems": [
            {"left_operand": 10 + index, "right_operand": 2, "operation": "subtract"}
            for index in range(20)
        ],
        "trivia_questions": [
            {
                "id": f"q{index}",
                "question": f"Question {index}?",
                "category": "General",
                "options": ["Alpha", "Beta", "Gamma", "Delta"],
                "correct_option_id": "A",
            }
            for index in range(5)
        ],
    }


def _assert_rejected(package: dict, message: str) -> None:
    with pytest.raises(PackageValidationError, match=message):
        validate_daily_package(package)


def test_valid_complete_package_is_accepted():
    package = _valid_package()
    validate_daily_package(package)
    assert UUID(package["package_revision_id"])


def test_pre_migration_playable_package_is_accepted_without_provenance():
    package = _valid_package()
    legacy_package = {
        key: package[key]
        for key in ("image_prompt", "image_url", "math_problems", "trivia_questions")
    }
    validate_playable_package(legacy_package)


def test_playable_package_rejects_an_unsafe_image_url():
    package = _valid_package()
    package["image_url"] = "http://example.test/image.png"
    with pytest.raises(PackageValidationError, match="absolute HTTPS URL"):
        validate_playable_package(package)


def test_negative_subtraction_is_rejected():
    package = _valid_package()
    package["math_problems"][0] = {"left_operand": 2, "right_operand": 10, "operation": "subtract"}
    _assert_rejected(package, "left_operand >= right_operand")


def test_unknown_operation_is_rejected():
    package = _valid_package()
    package["math_problems"][0]["operation"] = "power"
    _assert_rejected(package, "unknown operation")


def test_zero_division_is_rejected():
    package = _valid_package()
    package["math_problems"][0] = {"left_operand": 12, "right_operand": 0, "operation": "divide"}
    _assert_rejected(package, "divisor must not be zero")


def test_non_integral_division_is_rejected():
    package = _valid_package()
    package["math_problems"][0] = {"left_operand": 10, "right_operand": 3, "operation": "divide"}
    _assert_rejected(package, "integer result")


def test_stored_answer_must_match_derived_answer():
    package = _valid_package()
    package["math_problems"][0]["answer"] = 999
    _assert_rejected(package, "does not match derived answer")


def test_problem_count_must_equal_twenty():
    package = _valid_package()
    package["math_problems"].pop()
    _assert_rejected(package, "exactly 20")


@pytest.mark.parametrize("field", ["id", "question", "correct_option_id"])
def test_trivia_requires_stable_complete_question_identity(field):
    package = _valid_package()
    package["trivia_questions"][0][field] = ""
    _assert_rejected(package, field)


def test_trivia_rejects_duplicate_ids_and_options():
    package = _valid_package()
    package["trivia_questions"][1]["id"] = "q0"
    package["trivia_questions"][1]["options"] = ["Same", "same", "Third", "Fourth"]
    with pytest.raises(PackageValidationError) as exc_info:
        validate_daily_package(package)
    assert "duplicated" in str(exc_info.value)
    assert "unique" in str(exc_info.value)


def test_effective_prompt_is_the_scoring_prompt_when_present():
    package = _valid_package()
    package["effective_image_prompt"] = "Provider-revised cat prompt"
    package["image_prompt"] = "Provider-revised cat prompt"
    validate_daily_package(package)

    package["image_prompt"] = package["original_image_prompt"]
    _assert_rejected(package, "must equal effective_image_prompt")


@pytest.mark.parametrize(
    "field,value",
    [
        ("package_revision_id", "not-a-uuid"),
        ("original_image_prompt", ""),
        ("image_storage_key", "daily/2026-08-15/other.png"),
        ("image_url", "http://example.test/image.png"),
        ("image_sha256", "not-a-hash"),
        ("image_model", ""),
        ("image_generated_at", "yesterday"),
        ("image_verified_at", None),
    ],
)
def test_guess_identity_rejects_malformed_metadata(field, value):
    package = _valid_package()
    package[field] = value
    _assert_rejected(package, field)


def test_stale_schema_or_generator_version_is_rejected():
    stale_schema = _valid_package()
    stale_schema["content_schema_version"] -= 1
    _assert_rejected(stale_schema, "content_schema_version")

    stale_generator = deepcopy(_valid_package())
    stale_generator["generator_version"] = "legacy"
    _assert_rejected(stale_generator, "generator_version")
