#!/bin/bash
# Seed LocalStack with the AWS resources the MVP uses. Runs automatically when LocalStack is ready
# (mounted into /etc/localstack/init/ready.d/). awslocal ships inside the LocalStack image.
set -euo pipefail

echo "[init] seeding AWS resources..."

# S3 bucket for Agent Cards / deliverables
awslocal s3 mb s3://agent-cards || true

# KMS master key + alias for agent key custody
KEY_ID=$(awslocal kms create-key --description "agent-master" --query 'KeyMetadata.KeyId' --output text)
awslocal kms create-alias --alias-name alias/agent-master --target-key-id "$KEY_ID" || true

# DynamoDB table mirroring on-chain agent identities
awslocal dynamodb create-table \
  --table-name agents \
  --attribute-definitions AttributeName=name,AttributeType=S \
  --key-schema AttributeName=name,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST || true

# SQS queue for the settle keeper / indexer (used from M2)
awslocal sqs create-queue --queue-name settle || true

# DynamoDB table: per-day auto-refuel ledger (M3) — daily cap state checked before any transfer
awslocal dynamodb create-table \
  --table-name refuel-ledger \
  --attribute-definitions AttributeName=pk,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST || true

# DynamoDB table: append-only spend/event feed (M3) — read by the dashboard
awslocal dynamodb create-table \
  --table-name spend-events \
  --attribute-definitions AttributeName=pk,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST || true

echo "[init] localstack seeded"
