from __future__ import annotations

import argparse

import httpx

from telegram_trader.binance_market_data import BinanceMarketDataClient
from telegram_trader.binance_trading_client import create_trading_client
from telegram_trader.config import get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.execution_gateway import run_execution
from telegram_trader.logging_config import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 6 Slice 2a Execution Gateway: place a real Binance Testnet entry "
        "order for every RISK_APPROVED trade intent not yet submitted, confirm the fill, "
        "then place a STOP_MARKET protection order within 5 seconds -- emergency-closing "
        "the position if protection cannot be confirmed in time."
    )
    parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    market_data_client = BinanceMarketDataClient(
        httpx.Client(base_url=settings.binance_api_base_url, timeout=10.0)
    )
    trading_client = create_trading_client(settings)
    try:
        results = run_execution(
            create_session_factory(engine),
            settings=settings,
            market_data_client=market_data_client,
            trading_client=trading_client,
        )
        for result in results:
            print(
                f"intent_id={result.intent_id} symbol={result.symbol} "
                f"state={result.final_state} detail={result.detail}"
            )
        print(f"total_processed={len(results)}")
        return 0
    finally:
        market_data_client.close()
        trading_client.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
