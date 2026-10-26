import unittest

from src.research_contracts import (
    Personalization,
    ResearchIntent,
    ResearchRequest,
    SubjectRef,
    SubjectType,
)
from src.research_routing.acceptance import (
    SCENARIO_AS_OF as AS_OF,
    vision_question_scenarios,
)


def representative_requests() -> dict[str, ResearchRequest]:
    """The vision_q_v1 request of each frozen question in 长期愿景 Q1–Q10."""

    return {
        item.question_id: item.request
        for item in vision_question_scenarios()
    }


class ResearchRequestTest(unittest.TestCase):
    def test_representative_questions_round_trip(self):
        for question, request in representative_requests().items():
            with self.subTest(question=question):
                self.assertEqual(
                    ResearchRequest.from_dict(request.to_dict()),
                    request,
                )

    def test_required_personalization_needs_user(self):
        with self.assertRaisesRegex(ValueError, "user_id is required"):
            _request(
                subject_type=SubjectType.ASSET,
                intents=(ResearchIntent.DECIDE,),
                personalization=Personalization.REQUIRED,
            )

    def test_user_view_only_with_test_view(self):
        with self.assertRaisesRegex(ValueError, "only allowed with"):
            _request(
                intents=(ResearchIntent.EVALUATE,),
                user_id="user:a",
                user_view="我觉得会涨",
            )
        with self.assertRaisesRegex(ValueError, "requires user_view"):
            _request(intents=(ResearchIntent.TEST_VIEW,))
        with self.assertRaisesRegex(ValueError, "user_id is required"):
            _request(
                intents=(ResearchIntent.TEST_VIEW,),
                user_view="我觉得会涨",
            )

    def test_portfolio_requests_are_always_personal(self):
        with self.assertRaisesRegex(ValueError, "portfolio requests"):
            _request(
                subject_type=SubjectType.PORTFOLIO,
                subject_refs=(),
                intents=(ResearchIntent.PLAN,),
                personalization=Personalization.OPTIONAL,
                user_id="user:a",
            )

    def test_named_subjects_need_references(self):
        with self.assertRaisesRegex(ValueError, "need subject_refs"):
            _request(subject_type=SubjectType.STRATEGY, subject_refs=())

    def test_impersonal_request_rejects_personal_refs(self):
        with self.assertRaisesRegex(ValueError, "personal_refs"):
            _request(
                personalization=Personalization.NONE,
                user_id="user:a",
                personal_refs=("mandate:a",),
            )

    def test_from_dict_rejects_unknown_and_missing_fields(self):
        payload = representative_requests()["Q3"].to_dict()
        with self.assertRaisesRegex(ValueError, "Unknown ResearchRequest"):
            ResearchRequest.from_dict({**payload, "intent": "decide"})
        nested = {
            **payload,
            "subject_refs": [{"text": "600519", "code": "600519"}],
        }
        with self.assertRaisesRegex(ValueError, "Unknown SubjectRef"):
            ResearchRequest.from_dict(nested)
        del payload["intents"]
        with self.assertRaisesRegex(ValueError, "Missing ResearchRequest"):
            ResearchRequest.from_dict(payload)


def _request(**overrides) -> ResearchRequest:
    fields = {
        "request_id": "r",
        "subject_type": SubjectType.SECTOR,
        "subject_refs": (SubjectRef("快消"),),
        "intents": (ResearchIntent.UNDERSTAND,),
        "personalization": Personalization.OPTIONAL,
        "as_of_date": AS_OF,
    }
    fields.update(overrides)
    return ResearchRequest(**fields)


if __name__ == "__main__":
    unittest.main()
