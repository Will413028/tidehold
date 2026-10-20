"""A model provider that answers from prepared outputs, keyed by the wording it is asked about."""

import json
from typing import Any

from tidehold.policy.providers import Completion, ProviderError


class FakeModel:
    model = "fake-model"

    def __init__(self) -> None:
        # Wording -> the model's compile answer (a JSON text).
        self.answers: dict[str, str] = {}
        # The event values every event-set proposal returns.
        self.event_values: list[str] = []
        self.failing = False
        self.calls = 0

    def answer(self, wording: str, rules: list[dict[str, Any]], topics: list[str]) -> None:
        rulings = [
            {"topic": t, "question": f"How do you rule on {t}?", "quote": None} for t in topics
        ]
        self.answers[wording] = json.dumps({"rules": rules, "needs_ruling": rulings})

    async def complete(
        self, system: str, user: str, schema: dict[str, Any], name: str
    ) -> Completion:
        del system, schema
        self.calls += 1
        if self.failing:
            raise ProviderError("HTTP 503 UNAVAILABLE", 503)
        if name == "event_set":
            return Completion(json.dumps({"event_values": self.event_values}), 0.1)
        answer = next((a for w, a in self.answers.items() if w in user), None)
        return Completion(answer or json.dumps({"rules": [], "needs_ruling": []}), 0.1)
