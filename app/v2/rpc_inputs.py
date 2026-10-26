"""Pure trusted-backend RPC argument preparation; not an HTTP route or DB client."""
from dataclasses import dataclass
from .answers import normalize_answer
from .models import ChoiceFinalAnswer, TriviaFinalAnswer, TypedFinalAnswer


@dataclass(frozen=True)
class PreparedFinalAnswer:
    answer: dict
    normalized_text: str | None


def prepare_final_answer(answer: TypedFinalAnswer | ChoiceFinalAnswer | TriviaFinalAnswer) -> PreparedFinalAnswer:
    """Never take normalized_text from a client; derive it from validated raw text.

    No correctness, points or timing is accepted here. RPCs compare against their
    immutable solution and compute final points, timestamps and streak activity.
    """
    if not isinstance(answer,(TypedFinalAnswer,ChoiceFinalAnswer,TriviaFinalAnswer)):
        raise ValueError('Expected a validated final-answer model')
    return PreparedFinalAnswer(answer.model_dump(mode='json'),normalize_answer(answer.text) if isinstance(answer,TypedFinalAnswer) else None)
