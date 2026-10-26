import pytest
from pydantic import ValidationError
from app.v2.models import TypedFinalAnswer, ChoiceFinalAnswer, TriviaFinalAnswer
from app.v2.rpc_inputs import prepare_final_answer


@pytest.mark.parametrize('text,expected',[('  DIVISIBLE BY ３! ','divisible by 3'),('Straße?','strasse'),('ＪＡＰＡＮ','japan')])
def test_trusted_normalization_is_derived_from_raw_text(text,expected):
    result=prepare_final_answer(TypedFinalAnswer(kind='typed',text=text))
    assert result.answer=={'kind':'typed','text':text}
    assert result.normalized_text==expected


def test_client_cannot_add_a_normalized_answer_field():
    with pytest.raises(ValidationError):TypedFinalAnswer(kind='typed',text='wrong',normalized_text='correct')


def test_unvalidated_dict_not_accepted():
    with pytest.raises(ValueError):prepare_final_answer({'kind':'typed','text':'wrong'})


def test_choice_needs_no_normalized_text():
    assert prepare_final_answer(ChoiceFinalAnswer(kind='choice',selected_option_id='A')).normalized_text is None


def test_trivia_needs_no_normalized_text():
    answer=TriviaFinalAnswer.model_validate({'kind':'trivia','answers':[{'question_id':f'q{i}','selected_option_id':'A'} for i in range(5)]})
    assert prepare_final_answer(answer).normalized_text is None
