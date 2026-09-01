#!/usr/bin/env python3
"""
Запуск ETL-пайплайна для дашборда Ozon.
Использование:
    python run_etl.py [--days 30] [--shops shop_a,shop_b]
"""
import argparse
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

from etl.pipeline import run_pipeline  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)


def main():
    parser = argparse.ArgumentParser(description="Ozon Dashboard ETL")
    parser.add_argument("--days", type=int, default=60, help="Days back to fetch")
    parser.add_argument(
        "--shops", type=str, default="shop_a,shop_b", help="Comma-separated shop names"
    )
    args = parser.parse_args()

    shops = [s.strip() for s in args.shops.split(",") if s.strip()]
    run_pipeline(shops=shops, days_back=args.days)


if __name__ == "__main__":
    main()
