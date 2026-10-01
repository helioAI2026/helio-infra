#!/usr/bin/env bash
# Valida, empacota e implanta a infra do Helio; depois baixa o certificado do dispositivo.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${ALERT_EMAIL_1:?Defina ALERT_EMAIL_1 com o e-mail que recebe os alertas}"
PROJECT="${PROJECT_NAME:-helio}"
STACK="${STACK_NAME:-$PROJECT}"
REGION="${AWS_REGION:-us-east-1}"
THING="${THING_NAME:-helio-edge-01}"

echo "==> Validando templates"
uv run cfn-lint root.yaml stacks/*.yaml

echo "==> Chave e CSR do dispositivo"
mkdir -p certs build
if [ ! -f certs/device.key ]; then
  openssl req -new -newkey rsa:2048 -nodes \
    -keyout certs/device.key -out certs/device.csr -subj "/CN=$THING"
  chmod 600 certs/device.key
fi

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${PROJECT}-artifacts-${ACCOUNT}-${REGION}"
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "==> Criando bucket de artefatos $BUCKET"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  fi
fi

echo "==> Empacotando"
aws cloudformation package \
  --template-file root.yaml \
  --s3-bucket "$BUCKET" --s3-prefix "$STACK" \
  --output-template-file build/packaged.yaml \
  --region "$REGION" >/dev/null

echo "==> Implantando stack $STACK"
aws cloudformation deploy \
  --template-file build/packaged.yaml \
  --stack-name "$STACK" \
  --capabilities CAPABILITY_IAM \
  --region "$REGION" \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    "ProjectName=$PROJECT" \
    "DeviceThingName=$THING" \
    "DeviceCsr=$(cat certs/device.csr)" \
    "AlertEmail1=$ALERT_EMAIL_1" \
    "AlertEmail2=${ALERT_EMAIL_2:-}" \
    "AlertEmail3=${ALERT_EMAIL_3:-}"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

echo "==> Certificado do dispositivo"
aws iot describe-certificate --certificate-id "$(output CertificateId)" --region "$REGION" \
  --query certificateDescription.certificatePem --output text > certs/device.pem.crt
[ -f certs/AmazonRootCA1.pem ] || curl -fsSL https://www.amazontrust.com/repository/AmazonRootCA1.pem -o certs/AmazonRootCA1.pem
IOT_ENDPOINT=$(aws iot describe-endpoint --endpoint-type iot:Data-ATS --region "$REGION" --query endpointAddress --output text)
cat > certs/edge.env <<EOF
HELIO_IOT_ENDPOINT=$IOT_ENDPOINT
HELIO_THING_NAME=$THING
HELIO_CERT_PATH=$PWD/certs/device.pem.crt
HELIO_KEY_PATH=$PWD/certs/device.key
HELIO_CA_PATH=$PWD/certs/AmazonRootCA1.pem
EOF

echo "==> Pronto"
aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" --query "Stacks[0].Outputs" --output table
echo "Variáveis do edge em certs/edge.env. Confirme as inscrições do SNS nos e-mails recebidos."
