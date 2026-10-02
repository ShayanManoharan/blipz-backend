"""Pure validation for a complete Blipz daily-content package.

This module deliberately performs no database, storage, or network I/O.  Callers must
record ``image_verified_at`` only after the uploaded asset has passed the external
reachability check; this validator then requires that proof alongside the immutable
asset identity.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID


CONTENT_SCHEMA_VERSION = 2
GENERATOR_VERSION = "2026.08.14.1"
EXPECTED_MATH_PROBLEM_COUNT = 20
EXPECTED_TRIVIA_QUESTION_COUNT = 5
MATH_OPERATIONS = ("add", "subtract", "multiply", "divide")
TRIVIA_OPTION_IDS = ("A", "B", "C", "D")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class PackageValidationError(ValueError):
    """Raised when a daily package fails one or more serviceability invariants."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_timestamp(value: Any) -> bool:
    if isinstance(value, datetime):
        return value.tzinfo is not None
    if not _is_nonempty_string(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def compute_math_answer(left_operand: int, right_operand: int, operation: str) -> int:
    if operation == "add":
        return left_operand + right_operand
    if operation == "subtract":
        return left_operand - right_operand
    if operation == "multiply":
        return left_operand * right_operand
    if operation == "divide":
        if right_operand == 0:
            raise ValueError("division divisor must not be zero")
        if left_operand % right_operand != 0:
            raise ValueError("division must have an integer result")
        return left_operand // right_operand
    raise ValueError(f"unknown math operation: {operation!r}")


def validate_math_problems(problems: Any) -> None:
    errors: list[str] = []
    if not isinstance(problems, list):
        raise PackageValidationError(["math_problems must be a list"])
    if len(problems) != EXPECTED_MATH_PROBLEM_COUNT:
        errors.append(
            f"math_problems must contain exactly {EXPECTED_MATH_PROBLEM_COUNT} problems, got {len(problems)}"
        )

    for index, problem in enumerate(problems):
        prefix = f"math_problems[{index}]"
        if not isinstance(problem, dict):
            errors.append(f"{prefix} must be an object")
            continue

        left = problem.get("left_operand")
        right = problem.get("right_operand")
        operation = problem.get("operation")
        if not _is_int(left) or not _is_int(right):
            errors.append(f"{prefix} operands must be integers")
            continue
        if operation not in MATH_OPERATIONS:
            errors.append(f"{prefix} has unknown operation {operation!r}")
            continue
        if operation == "subtract" and left < right:
            errors.append(f"{prefix} subtraction must have left_operand >= right_operand")
            continue

        try:
            derived_answer = compute_math_answer(left, right, operation)
        except ValueError as exc:
            errors.append(f"{prefix} {exc}")
            continue
        if operation == "subtract" and derived_answer < 0:
            errors.append(f"{prefix} subtraction answer must be non-negative")
        if "answer" in problem:
            stored_answer = problem["answer"]
            if not _is_int(stored_answer):
                errors.append(f"{prefix}.answer must be an integer when present")
            elif stored_answer != derived_answer:
                errors.append(
                    f"{prefix}.answer {stored_answer} does not match derived answer {derived_answer}"
                )

    if errors:
        raise PackageValidationError(errors)


def validate_trivia_questions(questions: Any) -> None:
    errors: list[str] = []
    if not isinstance(questions, list):
        raise PackageValidationError(["trivia_questions must be a list"])
    if len(questions) != EXPECTED_TRIVIA_QUESTION_COUNT:
        errors.append(
            f"trivia_questions must contain exactly {EXPECTED_TRIVIA_QUESTION_COUNT} questions, got {len(questions)}"
        )

    seen_ids: set[str] = set()
    for index, question in enumerate(questions):
        prefix = f"trivia_questions[{index}]"
        if not isinstance(question, dict):
            errors.append(f"{prefix} must be an object")
            continue

        question_id = question.get("id")
        if not _is_nonempty_string(question_id):
            errors.append(f"{prefix}.id must be non-empty")
        elif question_id in seen_ids:
            errors.append(f"{prefix}.id {question_id!r} is duplicated")
        else:
            seen_ids.add(question_id)

        if not _is_nonempty_string(question.get("question")):
            errors.append(f"{prefix}.question must be non-empty")
        if not _is_nonempty_string(question.get("category")):
            errors.append(f"{prefix}.category must be non-empty")

        options = question.get("options")
        if not isinstance(options, list) or len(options) != 4:
            errors.append(f"{prefix}.options must contain exactly 4 values")
        else:
            normalized_options = [option.strip() if isinstance(option, str) else "" for option in options]
            if any(not option for option in normalized_options):
                errors.append(f"{prefix}.options must all be non-empty strings")
            if len({option.casefold() for option in normalized_options}) != len(normalized_options):
                errors.append(f"{prefix}.options must be unique")

        if question.get("correct_option_id") not in TRIVIA_OPTION_IDS:
            errors.append(f"{prefix}.correct_option_id must be one of A/B/C/D")

    if errors:
        raise PackageValidationError(errors)


def validate_guess_identity(package: dict[str, Any]) -> None:
    errors: list[str] = []
    original_prompt = package.get("original_image_prompt")
    effective_prompt = package.get("effective_image_prompt")
    scoring_prompt = package.get("image_prompt")
    if not _is_nonempty_string(original_prompt):
        errors.append("original_image_prompt must be non-empty")
    if effective_prompt is not None and not _is_nonempty_string(effective_prompt):
        errors.append("effective_image_prompt must be null or non-empty")

    expected_scoring_prompt = effective_prompt.strip() if _is_nonempty_string(effective_prompt) else (
        original_prompt.strip() if _is_nonempty_string(original_prompt) else None
    )
    if not _is_nonempty_string(scoring_prompt) or scoring_prompt.strip() != expected_scoring_prompt:
        errors.append("image_prompt must equal effective_image_prompt when present, otherwise original_image_prompt")

    revision = package.get("package_revision_id")
    try:
        UUID(str(revision))
    except (TypeError, ValueError, AttributeError):
        errors.append("package_revision_id must be a valid UUID")

    storage_key = package.get("image_storage_key")
    if not _is_nonempty_string(storage_key):
        errors.append("image_storage_key must be non-empty")
    elif _is_nonempty_string(str(revision)) and str(revision) not in storage_key:
        errors.append("image_storage_key must contain package_revision_id")

    image_url = package.get("image_url")
    if not _is_nonempty_string(image_url):
        errors.append("image_url must be non-empty")
    else:
        parsed_url = urlparse(image_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            errors.append("image_url must be an absolute HTTPS URL")
        if _is_nonempty_string(storage_key) and not parsed_url.path.endswith(storage_key):
            errors.append("image_url path must identify image_storage_key")

    image_sha256 = package.get("image_sha256")
    if not isinstance(image_sha256, str) or not SHA256_PATTERN.fullmatch(image_sha256):
        errors.append("image_sha256 must be a lowercase 64-character SHA-256 hex digest")
    if not _is_nonempty_string(package.get("image_model")):
        errors.append("image_model must be non-empty")
    if not _is_timestamp(package.get("image_generated_at")):
        errors.append("image_generated_at must be a timezone-aware timestamp")
    if not _is_timestamp(package.get("image_verified_at")):
        errors.append("image_verified_at must be a timezone-aware timestamp")

    if errors:
        raise PackageValidationError(errors)


def validate_playable_package(package: Any) -> None:
    """Validate the fields required to make all three games safely playable.

    This is the compatibility gate for rows created before the provenance migration
    is applied.  The complete validator below adds immutable image identity and
    version checks; callers must switch to that stricter gate once those columns are
    available in the database.
    """

    if not isinstance(package, dict):
        raise PackageValidationError(["daily package must be an object"])

    errors: list[str] = []
    if not _is_nonempty_string(package.get("image_prompt")):
        errors.append("image_prompt must be non-empty")

    image_url = package.get("image_url")
    if not _is_nonempty_string(image_url):
        errors.append("image_url must be non-empty")
    else:
        parsed_url = urlparse(image_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            errors.append("image_url must be an absolute HTTPS URL")

    for validator, value in (
        (validate_math_problems, package.get("math_problems")),
        (validate_trivia_questions, package.get("trivia_questions")),
    ):
        try:
            validator(value)
        except PackageValidationError as exc:
            errors.extend(exc.errors)

    if errors:
        raise PackageValidationError(errors)


def validate_daily_package(package: Any, *, require_current_versions: bool = True) -> None:
    """Validate every game and its technical provenance as one serviceable package."""

    if not isinstance(package, dict):
        raise PackageValidationError(["daily package must be an object"])

    errors: list[str] = []
    try:
        validate_playable_package(package)
    except PackageValidationError as exc:
        errors.extend(exc.errors)

    if require_current_versions:
        if package.get("content_schema_version") != CONTENT_SCHEMA_VERSION:
            errors.append(
                f"content_schema_version must be {CONTENT_SCHEMA_VERSION}, got {package.get('content_schema_version')!r}"
            )
        if package.get("generator_version") != GENERATOR_VERSION:
            errors.append(
                f"generator_version must be {GENERATOR_VERSION!r}, got {package.get('generator_version')!r}"
            )

    try:
        validate_guess_identity(package)
    except PackageValidationError as exc:
        errors.extend(exc.errors)

    if errors:
        raise PackageValidationError(errors)
