-- Runs only when the postgres_data volume is first created (docker-entrypoint-initdb.d).
-- The `test` compose service points at this database, never at `telegram_trader`:
-- tests/test_postgres_integration.py TRUNCATEs with CASCADE, which would otherwise
-- wipe real trade_intent/order rows. An existing volume needs this run once by hand
-- (see README "PostgreSQL Integration Tests").
CREATE DATABASE telegram_trader_test;
