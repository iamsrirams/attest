#!/usr/bin/env bash
# Deploy the Attest stack.
#
#   ./scripts/deploy.sh                 # dry run: show what would change
#   ./scripts/deploy.sh --apply         # create or update the stack
#   ./scripts/deploy.sh --apply --email you@example.com --enable-schedule
#
# The nightly schedule is created DISABLED unless --enable-schedule is passed.
# Enabling it means the agent starts sweeping unattended, which should be a
# deliberate choice made after watching a sweep run.

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

STACK="${ATTEST_STACK:-attest}"
REGION="${AWS_REGION:-us-east-1}"
PREFIX="${TABLE_PREFIX:-attest}"
DEMO_PREFIX="${DEMO_PREFIX:-attest-demo-}"
EMAIL=""
SCHEDULE="DISABLED"
APPLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --email) EMAIL="$2"; shift 2 ;;
    --enable-schedule) SCHEDULE="ENABLED"; shift ;;
    --stack) STACK="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

PARAMS=(
  "ParameterKey=Prefix,ParameterValue=$PREFIX"
  "ParameterKey=DemoPrefix,ParameterValue=$DEMO_PREFIX"
  "ParameterKey=ScheduleEnabled,ParameterValue=$SCHEDULE"
)
[[ -n "$EMAIL" ]] && PARAMS+=("ParameterKey=NotificationEmail,ParameterValue=$EMAIL")

echo "stack:    $STACK"
echo "region:   $REGION"
echo "schedule: $SCHEDULE"
echo

echo "validating template..."
aws cloudformation validate-template \
  --template-body file://infra/template.yaml \
  --region "$REGION" >/dev/null
echo "  ok"

if aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
     >/dev/null 2>&1; then
  CS_TYPE=UPDATE
else
  CS_TYPE=CREATE
fi

# scripts/bootstrap_aws.py creates the same tables and bucket outside
# CloudFormation, and CloudFormation will not adopt a resource it did not
# create. Check first, because the error it raises otherwise is an opaque
# "ResourceExistenceCheck" hook failure that says nothing about the cause.
if [[ $CS_TYPE == CREATE ]]; then
  CONFLICTS=()
  for t in runs controls evidence approvals audit_log; do
    if aws dynamodb describe-table --table-name "${PREFIX}_${t}" \
         --region "$REGION" >/dev/null 2>&1; then
      CONFLICTS+=("dynamodb table ${PREFIX}_${t}")
    fi
  done
  ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
  if aws s3api head-bucket --bucket "${PREFIX}-evidence-${ACCOUNT}" \
       --region "$REGION" >/dev/null 2>&1; then
    CONFLICTS+=("s3 bucket ${PREFIX}-evidence-${ACCOUNT}")
  fi

  if [[ ${#CONFLICTS[@]} -gt 0 ]]; then
    echo "These already exist outside CloudFormation:" >&2
    printf '  %s\n' "${CONFLICTS[@]}" >&2
    cat >&2 <<EOM

CloudFormation cannot adopt resources it did not create. Either:

  - deploy under a different prefix:
      TABLE_PREFIX=attestcf ./scripts/deploy.sh --apply

  - or remove the bootstrapped tables first, if you do not need their history:
      ./.venv/bin/python scripts/bootstrap_aws.py --clean
    (the evidence bucket is left alone; delete it by hand if you mean to)
EOM
    exit 1
  fi
fi

CS_NAME="deploy-$(date +%s)"
echo "creating $CS_TYPE change set $CS_NAME..."
aws cloudformation create-change-set \
  --stack-name "$STACK" \
  --template-body file://infra/template.yaml \
  --change-set-name "$CS_NAME" \
  --change-set-type "$CS_TYPE" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters "${PARAMS[@]}" \
  --region "$REGION" >/dev/null

# A change set with nothing to do fails the wait; that is a success, not an error.
if ! aws cloudformation wait change-set-create-complete \
       --stack-name "$STACK" --change-set-name "$CS_NAME" \
       --region "$REGION" 2>/dev/null; then
  REASON=$(aws cloudformation describe-change-set \
    --stack-name "$STACK" --change-set-name "$CS_NAME" \
    --region "$REGION" --query StatusReason --output text 2>/dev/null || true)
  if [[ "$REASON" == *"didn't contain changes"* || "$REASON" == *"No updates"* ]]; then
    echo "no changes to apply"
    aws cloudformation delete-change-set --stack-name "$STACK" \
      --change-set-name "$CS_NAME" --region "$REGION" >/dev/null 2>&1 || true
    exit 0
  fi
  echo "change set failed: $REASON" >&2
  exit 1
fi

echo
echo "changes:"
aws cloudformation describe-change-set \
  --stack-name "$STACK" --change-set-name "$CS_NAME" --region "$REGION" \
  --query 'Changes[].ResourceChange.[Action,ResourceType,LogicalResourceId]' \
  --output text | sed 's/^/  /'

if [[ $APPLY -eq 0 ]]; then
  echo
  echo "dry run only. re-run with --apply to execute."
  aws cloudformation delete-change-set --stack-name "$STACK" \
    --change-set-name "$CS_NAME" --region "$REGION" >/dev/null
  exit 0
fi

echo
echo "executing..."
aws cloudformation execute-change-set \
  --stack-name "$STACK" --change-set-name "$CS_NAME" --region "$REGION"

aws cloudformation wait "stack-${CS_TYPE,,}-complete" \
  --stack-name "$STACK" --region "$REGION"

echo
aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output text | sed 's/^/  /'

if [[ -n "$EMAIL" ]]; then
  echo
  echo "Verify $EMAIL in SES before approval emails can send:"
  echo "  aws ses verify-email-identity --email-address $EMAIL --region $REGION"
fi
