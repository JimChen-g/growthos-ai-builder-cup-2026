"""Deterministic Reddit/X feedback analysis for the Phase 3 demo."""

import re
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal
from functools import cache

from backend.models.feedback import (
    ClassifiedFeedback,
    CustomerProblem,
    FeedbackAnalysis,
    FeedbackSentiment,
    FeedbackSignal,
    FeedbackSource,
)

SENTIMENT_SCORES = {
    FeedbackSentiment.POSITIVE: Decimal("90"),
    FeedbackSentiment.MIXED: Decimal("70"),
    FeedbackSentiment.NEUTRAL: Decimal("50"),
    FeedbackSentiment.NEGATIVE: Decimal("30"),
}

POSITIVE_TERMS = {
    "love",
    "excellent",
    "great",
    "fast",
    "reliable",
    "helpful",
    "easy",
    "saves",
    "useful",
}
NEGATIVE_TERMS = {
    "confusing",
    "difficult",
    "slow",
    "broken",
    "frustrating",
    "hard",
    "unclear",
    "hate",
    "takes too long",
}
TOPIC_TERMS = {
    "onboarding": {"onboarding", "setup", "getting started", "first run"},
    "integrations": {"integration", "integrations", "connect", "connection"},
    "pricing": {"price", "pricing", "expensive", "billing"},
    "reliability": {"reliable", "reliability", "broken", "downtime", "bug"},
    "support": {"support", "response", "reply", "help"},
    "automation": {"automation", "agent", "workflow", "resolve"},
}
PROBLEM_LABELS = {
    "onboarding": "Onboarding difficulty",
    "integrations": "Integration setup friction",
    "pricing": "Pricing confusion",
    "reliability": "Reliability concerns",
    "support": "Support responsiveness",
    "automation": "Automation workflow friction",
    "general": "Uncategorized customer friction",
}
RECOMMENDATIONS = {
    "onboarding": "Simplify onboarding with a guided setup checklist and clearer first-run cues.",
    "integrations": "Prioritize the highest-friction integration and add connection diagnostics.",
    "pricing": "Clarify plan limits and show a simple value comparison before checkout.",
    "reliability": "Triage the cited reliability issue and publish a visible resolution update.",
    "support": "Improve response-time expectations and surface self-service answers sooner.",
    "automation": "Review the failing workflow and add clearer recovery guidance.",
    "general": "Review the cited feedback and validate the friction with customer interviews.",
}


NEGATORS = {
    "not",
    "no",
    "never",
    "hardly",
    "barely",
    "isn't",
    "wasn't",
    "aren't",
    "don't",
    "doesn't",
    "didn't",
    "can't",
    "won't",
    "isnt",
    "dont",
    "doesnt",
    "didnt",
    "cant",
}


@cache
def _term_pattern(term: str) -> re.Pattern[str]:
    # Whole words only (with simple plural/verb endings): "uneasy" is not "easy",
    # "breakfast" is not "fast", "helpful" is not the support topic "help".
    suffix = "" if " " in term else r"(?:s|es|ed|ing)?"
    return re.compile(rf"(?<![\w']){re.escape(term)}{suffix}(?![\w'])")


def _is_negated(text: str, start: int) -> bool:
    preceding = re.findall(r"[\w']+", text[:start])[-3:]
    return any(word in NEGATORS for word in preceding)


def _sentiment_hits(text: str) -> tuple[bool, bool]:
    positive = negative = False
    for term in POSITIVE_TERMS:
        for match in _term_pattern(term).finditer(text):
            if _is_negated(text, match.start()):
                negative = True  # "not great" reads as a complaint
            else:
                positive = True
    for term in NEGATIVE_TERMS:
        for match in _term_pattern(term).finditer(text):
            if _is_negated(text, match.start()):
                positive = True  # "not confusing" is mild praise
            else:
                negative = True
    return positive, negative


class FeedbackAgent:
    """Turn a bounded set of social signals into auditable customer intelligence."""

    def analyze(self, signals: list[FeedbackSignal]) -> FeedbackAnalysis:
        if not signals:
            raise ValueError("at least one Reddit or X feedback signal is required")

        classified = [self._classify(signal) for signal in signals]
        source_counts = Counter(item.source for item in signals)
        sentiment_counts = Counter(item.sentiment for item in classified)
        total = Decimal(len(classified))
        sentiment_score = self._rounded(
            sum((SENTIMENT_SCORES[item.sentiment] for item in classified), Decimal("0")) / total
        )
        positive_pct = self._rounded(
            Decimal(sentiment_counts[FeedbackSentiment.POSITIVE]) / total * 100
        )
        negative_pct = self._rounded(
            Decimal(sentiment_counts[FeedbackSentiment.NEGATIVE]) / total * 100
        )

        complaint_evidence: dict[str, list[str]] = defaultdict(list)
        for item in classified:
            if item.sentiment not in {FeedbackSentiment.NEGATIVE, FeedbackSentiment.MIXED}:
                continue
            complaint_topics = item.topics or ["general"]
            for topic in complaint_topics:
                complaint_evidence[topic].append(item.text)

        ordered = sorted(complaint_evidence.items(), key=lambda item: (-len(item[1]), item[0]))
        problems = [
            CustomerProblem(
                topic=topic,
                problem=PROBLEM_LABELS[topic],
                mentions=len(evidence),
                impact=self._impact(topic, len(evidence), len(signals)),
                evidence=evidence[:3],
            )
            for topic, evidence in ordered[:3]
        ]
        main_topic = problems[0].topic if problems else "general"
        main_problem = problems[0].problem if problems else "No dominant customer complaint"

        return FeedbackAnalysis(
            sample_count=len(signals),
            source_counts={source: source_counts[source] for source in FeedbackSource},
            overall_sentiment=self._overall_sentiment(sentiment_score),
            sentiment_score=sentiment_score,
            positive_pct=positive_pct,
            negative_pct=negative_pct,
            main_problem=main_problem,
            problems=problems,
            recommendation=(
                RECOMMENDATIONS[main_topic]
                if problems
                else "Continue monitoring Reddit and X for a repeated problem signal."
            ),
            analyzed_signals=classified,
        )

    @staticmethod
    def _classify(signal: FeedbackSignal) -> ClassifiedFeedback:
        normalized = signal.text.casefold()
        has_positive, has_negative = _sentiment_hits(normalized)
        if has_positive and has_negative:
            sentiment = FeedbackSentiment.MIXED
        elif has_negative:
            sentiment = FeedbackSentiment.NEGATIVE
        elif has_positive:
            sentiment = FeedbackSentiment.POSITIVE
        else:
            sentiment = FeedbackSentiment.NEUTRAL
        topics = [
            topic
            for topic, terms in TOPIC_TERMS.items()
            if any(_term_pattern(term).search(normalized) for term in terms)
        ]
        return ClassifiedFeedback(
            source=signal.source,
            text=signal.text,
            sentiment=sentiment,
            topics=topics,
        )

    @staticmethod
    def _impact(topic: str, mentions: int, total: int) -> str:
        if topic in {"onboarding", "reliability"} or mentions / total >= 0.4:
            return "high"
        return "medium" if mentions / total >= 0.2 else "low"

    @staticmethod
    def _overall_sentiment(score: Decimal) -> FeedbackSentiment:
        if score >= 65:
            return FeedbackSentiment.POSITIVE
        if score >= 45:
            return FeedbackSentiment.MIXED
        return FeedbackSentiment.NEGATIVE

    @staticmethod
    def _rounded(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
