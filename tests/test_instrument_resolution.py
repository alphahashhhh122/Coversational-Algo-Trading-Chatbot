from __future__ import annotations

import unittest

from iimc_trading_platform.orchestration import (
    _is_open_ended_advice,
    _normalize_intent_text,
)
from iimc_trading_platform.services.instrument_discovery_service import (
    _best_instrument_match,
)


class BestInstrumentMatchTest(unittest.TestCase):
    """The resolver must never return an unrelated instrument.

    A wrong-company quote (e.g. "colgate" -> DOLATALGO) is worse than an
    honest miss, so the fallback refuses instead of guessing matches[0].
    """

    UNRELATED = [
        {"symbol": "DOLATALGO", "name": "DOLAT ALGOTECH LIMITED"},
        {"symbol": "ITC", "name": "ITC LTD"},
    ]

    def test_unrelated_matches_are_refused(self) -> None:
        self.assertIsNone(_best_instrument_match(self.UNRELATED, "colgate"))
        self.assertIsNone(_best_instrument_match(self.UNRELATED, "netflix"))

    def test_name_prefix_resolves(self) -> None:
        matches = self.UNRELATED + [
            {"symbol": "COLPAL", "name": "COLGATE-PALMOLIVE (INDIA) LTD"},
        ]
        result = _best_instrument_match(matches, "colgate")
        self.assertIsNotNone(result)
        self.assertEqual(result["symbol"], "COLPAL")

    def test_exact_symbol_resolves(self) -> None:
        result = _best_instrument_match(
            [{"symbol": "RELIANCE", "name": "RELIANCE INDUSTRIES LTD"}],
            "reliance",
        )
        self.assertEqual(result["symbol"], "RELIANCE")

    def test_common_name_maps_to_ticker(self) -> None:
        result = _best_instrument_match(
            [{"symbol": "INFY", "name": "INFOSYS LIMITED"}],
            "infosys",
        )
        self.assertEqual(result["symbol"], "INFY")

    def test_empty_matches(self) -> None:
        self.assertIsNone(_best_instrument_match([], "anything"))


class OpenEndedAdviceTest(unittest.TestCase):
    """Vague 'what should I buy' asks get a clarifying reply, not a broker
    call that errors or a personalised stock pick."""

    def _advice(self, message: str) -> bool:
        return _is_open_ended_advice(_normalize_intent_text(message), message)

    def test_vague_advice_is_flagged(self) -> None:
        for message in (
            "which stock today is best upholding the style of rockefeller",
            "what should i buy today",
            "best stock to buy",
            "recommend a stock",
            "give me a multibagger",
        ):
            self.assertTrue(self._advice(message), message)

    def test_specific_asks_pass_through(self) -> None:
        for message in (
            "analyse RELIANCE fundamentally",
            "price of Reliance",
            "which is better, HDFCBANK or INFY",
            "buy 10 RELIANCE at market",
        ):
            self.assertFalse(self._advice(message), message)


if __name__ == "__main__":
    unittest.main()
