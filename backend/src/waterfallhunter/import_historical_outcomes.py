import argparse
import json

from waterfallhunter.core.historical_outcome_store import HistoricalOutcomeStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a provenance-labelled historical outcome report")
    parser.add_argument("report")
    parser.add_argument("--db-path", default="/app/data/waterfall_registry.db")
    args = parser.parse_args()
    result = HistoricalOutcomeStore(db_path=args.db_path).import_report_file(args.report)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
