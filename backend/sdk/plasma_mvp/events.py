"""EventLog — a small append-only spend/event feed backed by LocalStack DynamoDB (table `spend-events`).

The x402 resource server and the auto-refueler append here; the dashboard reads it for its live feed.
One row per event: `pk` (sortable id), `ts`, `kind`, and a JSON `data` blob.
"""
import json
import time

from .aws import Aws
from .config import load_config

EVENTS_TABLE = "spend-events"


class EventLog:
    def __init__(self, aws=None, cfg=None, table=None):
        self.cfg = cfg or load_config()
        self.aws = aws or Aws(self.cfg)
        self.table = table or EVENTS_TABLE
        self._seq = 0

    def _next_id(self) -> str:
        # millisecond clock + a per-instance counter keeps ids unique and roughly time-ordered
        self._seq += 1
        return "{:013d}-{:06d}".format(int(time.time() * 1000), self._seq)

    def record(self, kind: str, **data) -> str:
        ts = int(time.time())
        pk = self._next_id()
        self.aws.dynamodb.put_item(
            TableName=self.table,
            Item={
                "pk": {"S": pk},
                "ts": {"N": str(ts)},
                "kind": {"S": kind},
                "data": {"S": json.dumps(data, default=str)},
            },
        )
        return pk

    def list(self, limit: int = 50) -> list:
        resp = self.aws.dynamodb.scan(TableName=self.table, Limit=200)
        rows = []
        for it in resp.get("Items", []):
            rows.append({
                "pk": it["pk"]["S"],
                "ts": int(it["ts"]["N"]),
                "kind": it["kind"]["S"],
                "data": json.loads(it["data"]["S"]),
            })
        rows.sort(key=lambda r: r["pk"], reverse=True)
        return rows[:limit]
