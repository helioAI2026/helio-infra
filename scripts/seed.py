"""Popula os limiares padrão, dois motoristas de exemplo e o usuário administrador.

Uso: uv run python scripts/seed.py --admin-email voce@exemplo.com
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from common.severity import DEFAULT_THRESHOLDS  # noqa: E402


def stack_outputs(cloudformation, stack):
    outputs = cloudformation.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def put_if_absent(table, item, key):
    try:
        table.put_item(Item=item, ConditionExpression=f"attribute_not_exists({key})")
        print(f"criado: {item[key]}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        print(f"já existe: {item[key]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-name", default="Administrador Helio")
    parser.add_argument("--stack", default=os.environ.get("STACK_NAME", "helio"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    out = stack_outputs(session.client("cloudformation"), args.stack)
    dynamodb = session.resource("dynamodb")
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    put_if_absent(dynamodb.Table(out["SettingsTableName"]), {"settingId": "thresholds", **DEFAULT_THRESHOLDS}, "settingId")
    drivers = dynamodb.Table(out["DriversTableName"])
    put_if_absent(
        drivers,
        {
            "driverId": "drv_demo_ana",
            "name": "Ana Souza",
            "licenseNo": "12345678900",
            "phone": "+55 11 91234-5678",
            "assignedDeviceId": out["ThingName"],
            "createdAt": now,
        },
        "driverId",
    )
    put_if_absent(
        drivers,
        {"driverId": "drv_demo_bruno", "name": "Bruno Lima", "licenseNo": "98765432100", "phone": "+55 11 99876-5432", "createdAt": now},
        "driverId",
    )

    cognito = session.client("cognito-idp")
    try:
        cognito.admin_create_user(
            UserPoolId=out["UserPoolId"],
            Username=args.admin_email,
            UserAttributes=[
                {"Name": "email", "Value": args.admin_email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "name", "Value": args.admin_name},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
        print(f"usuário criado: {args.admin_email} (senha temporária enviada por e-mail)")
    except cognito.exceptions.UsernameExistsException:
        print(f"usuário já existe: {args.admin_email}")
    cognito.admin_add_user_to_group(UserPoolId=out["UserPoolId"], Username=args.admin_email, GroupName="Administrador")
    print("usuário no grupo Administrador")


if __name__ == "__main__":
    main()
