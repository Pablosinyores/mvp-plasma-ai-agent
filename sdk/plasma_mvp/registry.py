"""Off-chain agent index mirror (LocalStack DynamoDB).

A fast lookup table that mirrors on-chain identity for discovery. The chain remains source of truth;
this is a convenience cache the dashboard/discovery layer reads.
"""
from .aws import Aws
from .config import Config, load_config


class Registry:
    def __init__(self, aws: Aws = None, cfg: Config = None):
        self.cfg = cfg or load_config()
        self.aws = aws or Aws(self.cfg)
        self.table = self.cfg.ddb_table

    def put_agent(self, name: str, agent_id: int, address: str, card_uri: str) -> None:
        self.aws.dynamodb.put_item(
            TableName=self.table,
            Item={
                "name": {"S": name},
                "agentId": {"N": str(agent_id)},
                "address": {"S": address},
                "cardURI": {"S": card_uri},
                "status": {"S": "registered"},
            },
        )

    def _row(self, item):
        return {
            "name": item["name"]["S"],
            "agentId": int(item["agentId"]["N"]),
            "address": item["address"]["S"],
            "cardURI": item["cardURI"]["S"],
            "status": item.get("status", {}).get("S", ""),
        }

    def get_agent(self, name: str):
        resp = self.aws.dynamodb.get_item(TableName=self.table, Key={"name": {"S": name}})
        item = resp.get("Item")
        return self._row(item) if item else None

    def list_agents(self) -> list:
        resp = self.aws.dynamodb.scan(TableName=self.table)
        return [self._row(it) for it in resp.get("Items", [])]
