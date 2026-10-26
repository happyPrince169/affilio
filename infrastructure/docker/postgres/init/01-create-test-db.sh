#!/bin/sh
# Runs once on first container start (empty data volume).
# Creates a second database used by the API test-suite so tests never touch dev data.
set -eu

TEST_DB="${POSTGRES_TEST_DB:-affilio_test}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  SELECT 'CREATE DATABASE "$TEST_DB" OWNER "$POSTGRES_USER"'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$TEST_DB')\gexec
SQL

echo "[affilio] ensured test database '$TEST_DB' exists"
