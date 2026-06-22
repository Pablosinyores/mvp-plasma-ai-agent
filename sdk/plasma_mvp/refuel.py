"""AutoRefueler — keep an agent's gas/spend balance above a floor, within an owner-set daily cap.

When an agent's USDT balance drops below `floor`, the owner (a pre-authorized top-up account) sends a
fixed `refill`. A per-day cap lives in LocalStack DynamoDB (table `refuel-ledger`) and is checked —
and debited — **before any transfer**, so a refuel can never push the day's total over the cap even if
called in a tight loop (the design's safety requirement for autonomous spend).

The "day" key is derived from the chain block timestamp (not wall-clock) so it stays consistent with
on-chain time even when tests fast-forward Anvil; callers may override it for determinism.
"""
from .aws import Aws
from .config import load_config

REFUEL_TABLE = "refuel-ledger"


class RefuelLedger:
    def __init__(self, aws=None, cfg=None, table=None):
        self.cfg = cfg or load_config()
        self.aws = aws or Aws(self.cfg)
        self.table = table or REFUEL_TABLE

    @staticmethod
    def _pk(agent: str, day: str) -> str:
        return "{}#{}".format(agent.lower(), day)

    def spent(self, agent: str, day: str) -> int:
        resp = self.aws.dynamodb.get_item(
            TableName=self.table, Key={"pk": {"S": self._pk(agent, day)}}
        )
        item = resp.get("Item")
        return int(item["spent"]["N"]) if item else 0

    def add_spent(self, agent: str, day: str, amount: int) -> int:
        """Atomically add to the day's total; returns the new total."""
        resp = self.aws.dynamodb.update_item(
            TableName=self.table,
            Key={"pk": {"S": self._pk(agent, day)}},
            UpdateExpression="ADD #s :a",
            ExpressionAttributeNames={"#s": "spent"},
            ExpressionAttributeValues={":a": {"N": str(int(amount))}},
            ReturnValues="UPDATED_NEW",
        )
        return int(resp["Attributes"]["spent"]["N"])


class AutoRefueler:
    def __init__(self, adapter, owner_account, floor, refill, daily_cap, ledger=None, cfg=None,
                 events=None):
        self.adapter = adapter
        self.owner = owner_account
        self.floor = int(floor)
        self.refill = int(refill)
        self.daily_cap = int(daily_cap)
        self.cfg = cfg or load_config()
        self.ledger = ledger or RefuelLedger(cfg=self.cfg)
        self.events = events

    def _chain_day(self) -> str:
        ts = self.adapter.w3.eth.get_block("latest")["timestamp"]
        return "day-{}".format(ts // 86400)

    def maybe_refuel(self, agent_address: str, day: str = None) -> dict:
        """Refuel `agent_address` if below floor AND within the daily cap. Returns an outcome dict."""
        day = day or self._chain_day()
        balance = self.adapter.usdt_balance(agent_address)
        if balance >= self.floor:
            return {"refueled": False, "reason": "above floor", "balance": balance}

        spent_today = self.ledger.spent(agent_address, day)
        # cap enforced BEFORE any transfer — a refuel that would breach the cap never sends funds
        if spent_today + self.refill > self.daily_cap:
            return {
                "refueled": False, "reason": "daily cap reached",
                "balance": balance, "spent_today": spent_today, "daily_cap": self.daily_cap,
            }

        new_total = self.ledger.add_spent(agent_address, day, self.refill)
        tx = self.adapter.transfer_usdt(self.owner, agent_address, self.refill)
        if self.events is not None:
            self.events.record(
                kind="refuel", owner=self.owner.address, agent=agent_address,
                amount=self.refill, tx=tx,
            )
        return {
            "refueled": True, "amount": self.refill, "tx": tx,
            "balance_before": balance, "spent_today": new_total, "day": day,
        }
