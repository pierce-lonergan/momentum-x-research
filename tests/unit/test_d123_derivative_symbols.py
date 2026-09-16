"""D123: Warrant/preferred/unit symbol cleaning tests.

Bug: Alpaca movers endpoint returns derivative symbols (SLND.WS, ANNAW)
that the equities snapshot API can't quote. These silently disappeared,
causing the system to miss the underlying common stock.

SLND.WS was the #1 gainer on Mar 23 (SLND common +27%) but the system
never evaluated SLND because it stored the warrant symbol.
"""

from main import _clean_derivative_symbols


class TestCleanDerivativeSymbols:
    """D123: _clean_derivative_symbols maps derivatives to common tickers."""

    def test_ws_warrant_suffix(self):
        """SLND.WS → SLND (the actual Mar 23 bug)."""
        assert _clean_derivative_symbols(["SLND.WS"]) == ["SLND"]

    def test_ws_a_warrant_suffix(self):
        """SPKL.WS.A → SPKL."""
        assert _clean_derivative_symbols(["SPKL.WS.A"]) == ["SPKL"]

    def test_trailing_w_preserved(self):
        """D126: Trailing-W rule REMOVED — corrupted MKDW → MKD.
        ANNAW stays as ANNAW (will fail snapshot, ANNA caught separately)."""
        assert _clean_derivative_symbols(["ANNAW"]) == ["ANNAW"]
        assert _clean_derivative_symbols(["MKDW"]) == ["MKDW"]

    def test_short_ticker_w_preserved(self):
        """Short tickers ending in W are real stocks, not warrants."""
        # "W" (Wayfair), "VW", "BMW" — should NOT be stripped
        assert _clean_derivative_symbols(["W"]) == ["W"]
        assert _clean_derivative_symbols(["VW"]) == ["VW"]
        assert _clean_derivative_symbols(["BMW"]) == ["BMW"]

    def test_unit_suffix_u(self):
        """SPKL.U → SPKL."""
        assert _clean_derivative_symbols(["SPKL.U"]) == ["SPKL"]

    def test_unit_suffix_un(self):
        """SPKL.UN → SPKL."""
        assert _clean_derivative_symbols(["SPKL.UN"]) == ["SPKL"]

    def test_regular_tickers_unchanged(self):
        """Normal tickers pass through unchanged."""
        tickers = ["AAPL", "TSLA", "JDZG", "IBO", "ANNA"]
        assert _clean_derivative_symbols(tickers) == tickers

    def test_deduplication_ws(self):
        """SLND and SLND.WS both map to SLND — only one should remain."""
        result = _clean_derivative_symbols(["SLND", "SLND.WS", "IBO"])
        assert result == ["SLND", "IBO"]

    def test_annaw_kept_as_separate_ticker(self):
        """D126: ANNAW is kept (trailing-W rule removed). ANNA is separate."""
        result = _clean_derivative_symbols(["ANNAW", "IBO", "ANNA"])
        assert result == ["ANNAW", "IBO", "ANNA"]

    def test_empty_list(self):
        assert _clean_derivative_symbols([]) == []

    def test_real_mar23_movers(self):
        """Reproduce the actual Mar 23 movers list."""
        raw = ["JAN", "ANNAW", "ANNA", "IBO", "SLND.WS", "SMCZ",
               "JDZG", "PLU", "VG", "UGRO"]
        result = _clean_derivative_symbols(raw)
        # SLND.WS → SLND (the original bug fix)
        # D126: ANNAW kept as-is (trailing-W rule removed)
        assert "SLND" in result, "SLND.WS should map to SLND"
        assert "SLND.WS" not in result
        assert "ANNAW" in result, "D126: ANNAW kept (trailing-W removed)"
        assert "ANNA" in result, "ANNA still present independently"
