from __future__ import annotations

import argparse

import httpx

from telegram_trader.binance_market_data import BinanceMarketDataClient
from telegram_trader.config import get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.logging_config import configure_logging
from telegram_trader.trade_intent_pipeline import run_risk_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Turn every APPROVED signal decision into a TradeIntent, evaluate it "
        "against fixed risk rules, and record a RiskDecision (Phase 6 Slice 1 -- Risk "
        "Engine). No Binance credential is touched: mark-price is a public endpoint."
    )
    parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    http_client = httpx.Client(base_url=settings.binance_api_base_url, timeout=10.0)
    market_data_client = BinanceMarketDataClient(http_client)
    try:
        results = run_risk_evaluation(
            create_session_factory(engine),
            settings=settings,
            market_data_client=market_data_client,
        )
        for result in results:
            print(
                f"intent_id={result.intent_id} symbol={result.symbol} "
                f"verdict={result.verdict} reasons={','.join(result.reason_codes) or '-'}"
            )
        print(f"total_evaluated={len(results)}")
        return 0
    finally:
        market_data_client.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
