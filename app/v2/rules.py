"""The initial bounded Hidden Rule family. Never evaluates generated code."""
from .models import HiddenRulePublicPayload, HiddenRulePrivateSolution, RuleSpec

MAX_DOMAIN_SIZE = 10000


def evaluate_integer_rule(value: int, spec: RuleSpec) -> bool:
    if type(value) is not int:
        raise ValueError("Rule input must be an integer")
    # RuleSpec admits only the implemented family and a divisor from 2 through 99.
    return value % spec.divisor == 0


def validate_hidden_rule(public: HiddenRulePublicPayload, private: HiddenRulePrivateSolution) -> None:
    from .validation import validate_answer_choices

    validate_answer_choices(private)
    low, high = public.domain.minimum, public.domain.maximum
    if high < low or high - low + 1 > MAX_DOMAIN_SIZE:
        raise ValueError("Rule domain must be ordered and contain at most 10000 integers")
    examples = public.belongs + public.does_not_belong
    if len(set(examples)) != len(examples):
        raise ValueError("Duplicate or contradictory rule examples")
    if any(not low <= value <= high for value in examples):
        raise ValueError("Rule example outside declared domain")
    if not all(evaluate_integer_rule(value, private.rule_spec) for value in public.belongs):
        raise ValueError("Positive example fails intended rule")
    if any(evaluate_integer_rule(value, private.rule_spec) for value in public.does_not_belong):
        raise ValueError("Negative example satisfies intended rule")

    domain = range(low, high + 1)
    signatures = [tuple(evaluate_integer_rule(v, choice.rule_spec) for v in domain) for choice in private.choices]
    if len(set(signatures)) != 4:
        raise ValueError("Equivalent MCQ predicates within declared domain")
    fitting = [choice for choice in private.choices if all(evaluate_integer_rule(v, choice.rule_spec) for v in public.belongs) and all(not evaluate_integer_rule(v, choice.rule_spec) for v in public.does_not_belong)]
    if len(fitting) != 1 or fitting[0].id != private.correct_choice_id:
        raise ValueError("Exactly the intended MCQ choice must fit every example")
    intended_signature = tuple(evaluate_integer_rule(v, private.rule_spec) for v in domain)
    if tuple(evaluate_integer_rule(v, fitting[0].rule_spec) for v in domain) != intended_signature:
        raise ValueError("Correct MCQ predicate differs from intended executable rule")
