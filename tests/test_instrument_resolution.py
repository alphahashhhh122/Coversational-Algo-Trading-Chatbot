from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
