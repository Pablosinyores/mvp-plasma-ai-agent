"""boto3 clients wired to LocalStack.

Every client reads endpoint_url + dummy credentials from Config. The SAME code talks to real AWS in
production by clearing AWS_ENDPOINT_URL and supplying real credentials — no code change.
"""
import boto3

from .config import Config, load_config


class Aws:
    def __init__(self, cfg: Config = None):
        self.cfg = cfg or load_config()
        creds = self.cfg.aws_creds
        self.s3 = boto3.client("s3", **creds)
        self.kms = boto3.client("kms", **creds)
        self.secrets = boto3.client("secretsmanager", **creds)
        self.dynamodb = boto3.client("dynamodb", **creds)
        self.sqs = boto3.client("sqs", **creds)

    def ping(self) -> bool:
        """Cheap reachability check against the LocalStack endpoint."""
        self.s3.list_buckets()
        return True
