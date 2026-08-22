#!/usr/bin/env bash
set -euo pipefail

profile="plotline-agent"
region="ap-south-1"
bucket="plotline-kb-apsouth1"
table="plotline_chunks"

if aws s3api head-bucket --bucket "$bucket" --profile "$profile" >/dev/null 2>&1; then
  aws s3 rm "s3://$bucket" --recursive --profile "$profile"
  aws s3api delete-bucket --bucket "$bucket" --region "$region" --profile "$profile"
fi

if aws dynamodb describe-table --table-name "$table" --region "$region" --profile "$profile" >/dev/null 2>&1; then
  aws dynamodb delete-table --table-name "$table" --region "$region" --profile "$profile" >/dev/null
  aws dynamodb wait table-not-exists --table-name "$table" --region "$region" --profile "$profile"
fi

echo "Deleted Plotline AWS resources created by this project."

