#!/usr/bin/env bash
set -euo pipefail

profile="plotline-agent"
region="ap-south-1"
bucket="plotline-kb-apsouth1"
table="plotline_chunks"

account_id="$(aws sts get-caller-identity --profile "$profile" --query Account --output text)"

if ! aws s3api head-bucket --bucket "$bucket" --profile "$profile" >/dev/null 2>&1; then
  if ! aws s3api create-bucket --bucket "$bucket" --region "$region" \
    --create-bucket-configuration "LocationConstraint=$region" \
    --profile "$profile" >/dev/null; then
    bucket="plotline-kb-apsouth1-$account_id"
    aws s3api head-bucket --bucket "$bucket" --profile "$profile" >/dev/null 2>&1 || \
      aws s3api create-bucket --bucket "$bucket" --region "$region" \
        --create-bucket-configuration "LocationConstraint=$region" \
        --profile "$profile" >/dev/null
  fi
fi

plotline_tables="$(aws dynamodb list-tables --region "$region" --profile "$profile" \
  --query 'TableNames[?starts_with(@, `plotline_`)]' --output text)"
total_rcu=0
total_wcu=0
for existing in $plotline_tables; do
  billing_mode="$(aws dynamodb describe-table --table-name "$existing" --region "$region" \
    --profile "$profile" --query 'Table.BillingModeSummary.BillingMode' --output text)"
  if [[ "$billing_mode" == "PAY_PER_REQUEST" ]]; then
    echo "Refusing setup: $existing uses forbidden on-demand billing." >&2
    exit 1
  fi
  read -r rcu wcu < <(aws dynamodb describe-table --table-name "$existing" --region "$region" \
    --profile "$profile" --query 'Table.[ProvisionedThroughput.ReadCapacityUnits,ProvisionedThroughput.WriteCapacityUnits]' --output text)
  total_rcu=$((total_rcu + rcu))
  total_wcu=$((total_wcu + wcu))
done

if ! aws dynamodb describe-table --table-name "$table" --region "$region" --profile "$profile" >/dev/null 2>&1; then
  if (( total_rcu + 1 > 10 || total_wcu + 1 > 10 )); then
    echo "Refusing table creation: Plotline provisioned-capacity cap would be exceeded." >&2
    exit 1
  fi
  aws dynamodb create-table --table-name "$table" --region "$region" \
    --attribute-definitions AttributeName=source_id_full,AttributeType=S \
    --key-schema AttributeName=source_id_full,KeyType=HASH \
    --provisioned-throughput ReadCapacityUnits=1,WriteCapacityUnits=1 \
    --profile "$profile" >/dev/null
  aws dynamodb wait table-exists --table-name "$table" --region "$region" --profile "$profile"
fi

read -r target_rcu target_wcu < <(aws dynamodb describe-table --table-name "$table" --region "$region" \
  --profile "$profile" --query 'Table.[ProvisionedThroughput.ReadCapacityUnits,ProvisionedThroughput.WriteCapacityUnits]' --output text)
read -r key_name key_type < <(aws dynamodb describe-table --table-name "$table" --region "$region" \
  --profile "$profile" --query 'Table.KeySchema[0].[AttributeName,KeyType]' --output text)
if [[ "$target_rcu" != "1" || "$target_wcu" != "1" || "$key_name" != "source_id_full" || "$key_type" != "HASH" ]]; then
  echo "Refusing setup: $table must use source_id_full (String HASH) at exactly 1 RCU / 1 WCU." >&2
  exit 1
fi

echo "bucket=$bucket"
echo "table=$table"
