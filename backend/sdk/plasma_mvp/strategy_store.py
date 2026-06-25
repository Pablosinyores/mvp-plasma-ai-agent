"""StrategyStore — durable persistence for a Trader's standing strategy + one-off fire state.

Keyed by the agent's own address, a record is::

    {"strategy": dict|None,   # the parsed order (swap/dca/rebalance/limit/noop) or None
     "prompt":   str|None,    # the natural-language prompt it came from (for the UI)
     "tickCount": int,        # so cadence/age survives a restart
     "swapDone":  bool}       # so a one-off swap / triggered limit is NOT re-fired after restart

Two interchangeable backends behind the SAME interface (save/load/delete):
  * FileStrategyStore  — a JSON file under the repo; works with no Docker / no AWS (the demo default).
  * DynamoStrategyStore — LocalStack/AWS DynamoDB, a drop-in for the file store when the table is up.

`open_strategy_store()` prefers DynamoDB and silently falls back to the file store when the endpoint
is unreachable, so the trader persists either way and the tests run without Docker.
"""
import json
import os
from pathlib import Path

from .config import Config, load_config


def _norm_addr(address: str) -> str:
    return str(address).lower()


class FileStrategyStore:
    """JSON-file strategy store. One file holds an {address: record} map; writes are atomic-ish
    (write temp + replace) so a crash mid-write can't corrupt the existing strategies."""

    def __init__(self, path=None, cfg: Config = None):
        cfg = cfg or load_config()
        default = cfg.deployments_path.parent.parent / "strategies.json"  # contracts/strategies.json
        self.path = Path(path) if path else Path(default)

    def _read_all(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text() or "{}")
        except (ValueError, OSError):
            return {}

    def _write_all(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)

    def save(self, address: str, record: dict) -> None:
        data = self._read_all()
        data[_norm_addr(address)] = record
        self._write_all(data)

    def load(self, address: str):
        return self._read_all().get(_norm_addr(address))

    def delete(self, address: str) -> None:
        data = self._read_all()
        if data.pop(_norm_addr(address), None) is not None:
            self._write_all(data)


class DynamoStrategyStore:
    """DynamoDB-backed strategy store (drop-in for FileStrategyStore). The whole record is stashed as
    one JSON blob under a single attribute, so the schema never needs migrating as strategies grow."""

    def __init__(self, aws=None, cfg: Config = None, table: str = None):
        from .aws import Aws  # local import: only needed on the Dynamo path
        self.cfg = cfg or load_config()
        self.aws = aws or Aws(self.cfg)
        self.table = table or (self.cfg.ddb_table + "-strategies")

    def ensure_table(self) -> None:
        existing = self.aws.dynamodb.list_tables().get("TableNames", [])
        if self.table in existing:
            return
        self.aws.dynamodb.create_table(
            TableName=self.table,
            KeySchema=[{"AttributeName": "address", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "address", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        self.aws.dynamodb.get_waiter("table_exists").wait(TableName=self.table)

    def save(self, address: str, record: dict) -> None:
        self.aws.dynamodb.put_item(
            TableName=self.table,
            Item={"address": {"S": _norm_addr(address)}, "record": {"S": json.dumps(record)}},
        )

    def load(self, address: str):
        resp = self.aws.dynamodb.get_item(
            TableName=self.table, Key={"address": {"S": _norm_addr(address)}}
        )
        item = resp.get("Item")
        return json.loads(item["record"]["S"]) if item else None

    def delete(self, address: str) -> None:
        self.aws.dynamodb.delete_item(
            TableName=self.table, Key={"address": {"S": _norm_addr(address)}}
        )


def open_strategy_store(cfg: Config = None, prefer_dynamo: bool = True):
    """Return the best available strategy store. Tries DynamoDB (and creates the table) when reachable;
    falls back to the JSON-file store when the endpoint is down — so persistence works with or without
    Docker, and the Dynamo impl is a true drop-in once LocalStack/AWS is up."""
    cfg = cfg or load_config()
    if prefer_dynamo:
        try:
            store = DynamoStrategyStore(cfg=cfg)
            store.ensure_table()
            return store
        except Exception:  # noqa: BLE001 — endpoint down / no creds -> file store
            pass
    return FileStrategyStore(cfg=cfg)
