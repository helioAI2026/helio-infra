#!/usr/bin/env bash
# Publica um alerta falso no tópico do dispositivo e confere se a viagem chegou ao DynamoDB.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${PROJECT_NAME:-helio}"
STACK="${STACK_NAME:-$PROJECT}"
REGION="${AWS_REGION:-us-east-1}"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

THING=$(output ThingName)
TRIPS=$(output TripsTableName)
ENDPOINT=$(aws iot describe-endpoint --endpoint-type iot:Data-ATS --region "$REGION" --query endpointAddress --output text)
RIDE="smoke-$(date +%s)"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
PAYLOAD=$(jq -nc --arg ride "$RIDE" --arg ts "$NOW" --arg thing "$THING" \
  '{device_id: $thing, ride_id: $ride, timestamp: (now | floor), nonce: "00000000",
    data: [{timestamp: $ts, score: 0.91, ear: 0.12, perclos: 0.55, status: "ALERTA", durationSec: 3.2}]}')

echo "==> Publicando em helio/devices/$THING/events (viagem $RIDE)"
aws iot-data publish --endpoint-url "https://$ENDPOINT" --region "$REGION" \
  --topic "helio/devices/$THING/events" --cli-binary-format raw-in-base64-out --payload "$PAYLOAD"

for _ in $(seq 1 15); do
  COUNT=$(aws dynamodb get-item --table-name "$TRIPS" --region "$REGION" \
    --key "{\"tripId\": {\"S\": \"$RIDE\"}}" --query Item.alertCount.N --output text)
  if [ "$COUNT" = "1" ]; then
    echo "OK: viagem $RIDE registrada com 1 alerta. Confira o e-mail '[Helio] Alerta critico: ...'."
    exit 0
  fi
  sleep 2
done

echo "FALHA: a viagem $RIDE não apareceu em $TRIPS."
echo "Logs: aws logs tail /aws/lambda/${PROJECT}-ingest --region $REGION"
echo "      aws logs tail /aws/iot/${PROJECT}-rule-errors --region $REGION"
exit 1
