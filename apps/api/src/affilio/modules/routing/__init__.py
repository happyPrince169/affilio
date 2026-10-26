"""routing — deterministic channel recommendations for classified final videos (M3A.1).

Rule-based and explainable: no LLM, no external API, no engagement prediction. Scores are
routing relevance only. The engine (`accounts/routing_engine.py`) is a pure function so
M4/M5 can replace or re-rank its output without touching the API.
or re-rank its output without touching the API.
"""
