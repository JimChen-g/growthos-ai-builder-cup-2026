"""Map a founder's own words onto the growth questions the connected data can answer.

The agent must respond to what the founder actually asked. Topics the connected sources
cannot diagnose (for example churn without retention data) are named explicitly instead
of being silently replaced by a different diagnosis.
"""

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

TOPIC_PATTERNS: dict[str, tuple[str, ...]] = {
    "acquisition": (
        r"\bcac\b",
        r"\bcpa\b",
        r"acquisition",
        r"\bads?\b",
        r"paid",
        r"cost per",
        r"\bleads?\b",
        r"roas",
        r"campaign",
        r"获客|獲客|广告|廣告|投放|线索|線索",
    ),
    "visibility": (
        r"visibility",
        r"\bseo\b",
        r"\bgeo\b",
        r"ai search",
        r"chatgpt",
        r"perplexity",
        r"gemini",
        r"discover",
        r"\bbrand\b",
        r"可见度|可見度|曝光|搜索|搜尋|品牌",
    ),
    "revenue": (
        r"revenue",
        r"\bmrr\b",
        r"\barr\b",
        r"growth (?:is )?slow",
        r"slowing",
        r"收入|营收|營收|增长放缓|成長放緩|增长变慢|成長變慢",
    ),
    "retention": (
        r"churn",
        r"retention",
        r"cancel",
        r"renewal",
        r"downgrade",
        r"流失|留存|续费|續費|退订|退訂",
    ),
    "pricing": (r"pricing", r"\bprice", r"discount", r"billing", r"定价|定價|价格|價格|折扣"),
    "activation": (
        r"onboarding",
        r"activation",
        r"\btrial",
        r"conversion rate",
        r"setup",
        r"上手|激活|啟用|试用|試用|转化率|轉換率|轉化率",
    ),
}

TOPIC_LABELS = {
    "acquisition": "acquisition cost",
    "visibility": "AI visibility",
    "revenue": "revenue momentum",
    "retention": "churn/retention",
    "pricing": "pricing",
    "activation": "onboarding/activation",
}

# What Stripe, Google Ads, Meta Ads and the GEO scan can currently diagnose.
COVERED_TOPICS = frozenset({"acquisition", "visibility", "revenue"})


@dataclass(frozen=True, slots=True)
class FounderIntent:
    topics: tuple[str, ...]

    @property
    def covered(self) -> tuple[str, ...]:
        return tuple(topic for topic in self.topics if topic in COVERED_TOPICS)

    @property
    def uncovered(self) -> tuple[str, ...]:
        return tuple(topic for topic in self.topics if topic not in COVERED_TOPICS)

    @property
    def leads_with_visibility(self) -> bool:
        return bool(self.covered) and self.covered[0] == "visibility"


def analyze_founder_message(message: str) -> FounderIntent:
    """Return the topics in the order the founder mentioned them."""

    text = message.casefold()
    positions: list[tuple[int, str]] = []
    for topic, patterns in TOPIC_PATTERNS.items():
        matches = [m.start() for p in patterns if (m := re.search(p, text))]
        if matches:
            positions.append((min(matches), topic))
    return FounderIntent(topics=tuple(topic for _, topic in sorted(positions)))


def _join(labels: list[str]) -> str:
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def deterministic_acknowledgement(intent: FounderIntent, primary_finding: str) -> str:
    """A grounded reply that addresses the founder's topics without inventing causes."""

    covered = set(intent.covered)
    if not intent.uncovered and covered >= {"acquisition", "visibility"}:
        return "I found the acquisition and AI-visibility constraints behind the slowdown."
    parts: list[str] = []
    if intent.uncovered:
        missing = _join([TOPIC_LABELS[topic] for topic in intent.uncovered])
        parts.append(
            f"You raised {missing}; the connected sources don't include that data yet, "
            "so I can't diagnose it reliably."
        )
    if intent.covered:
        asked = _join([TOPIC_LABELS[topic] for topic in intent.covered])
        parts.append(f"On {asked}, the data points to {primary_finding}.")
    else:
        parts.append(f"What the connected data does show is {primary_finding}.")
    return " ".join(parts)


class AcknowledgementNarrative(BaseModel):
    """Schema for an optional Gemini-written acknowledgement."""

    model_config = ConfigDict(extra="forbid")

    acknowledgement: str = Field(min_length=1, max_length=400)
