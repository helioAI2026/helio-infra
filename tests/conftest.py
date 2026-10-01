import json
import os

import boto3
import pytest
from moto import mock_aws

os.environ.update(
    {
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "DRIVERS_TABLE": "helio-drivers",
        "TRIPS_TABLE": "helio-trips",
        "EVENTS_TABLE": "helio-events",
        "SETTINGS_TABLE": "helio-settings",
        "DASHBOARD_URL": "https://helio.example",
    }
)


def _create_table(client, name, key, attributes, indexes=()):
    """Espelha stacks/data.yaml. Mantenha os dois em sincronia."""
    kwargs = {
        "TableName": name,
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [{"AttributeName": a, "AttributeType": "S"} for a in attributes],
        "KeySchema": [{"AttributeName": key, "KeyType": "HASH"}],
    }
    if indexes:
        kwargs["GlobalSecondaryIndexes"] = [
            {
                "IndexName": index,
                "KeySchema": [{"AttributeName": hash_key, "KeyType": "HASH"}]
                + ([{"AttributeName": range_key, "KeyType": "RANGE"}] if range_key else []),
                "Projection": {"ProjectionType": "ALL"},
            }
            for index, hash_key, range_key in indexes
        ]
    client.create_table(**kwargs)


@pytest.fixture
def aws():
    with mock_aws():
        from common import db

        db.reset()
        ddb = boto3.client("dynamodb")
        _create_table(ddb, "helio-drivers", "driverId", ["driverId", "assignedDeviceId"], [("byDevice", "assignedDeviceId", None)])
        _create_table(ddb, "helio-trips", "tripId", ["tripId", "driverId", "startedAt"], [("byDriver", "driverId", "startedAt")])
        _create_table(
            ddb,
            "helio-events",
            "eventId",
            ["eventId", "tripId", "driverId", "day", "timestamp"],
            [("byTrip", "tripId", "timestamp"), ("byDriver", "driverId", "timestamp"), ("byDay", "day", "timestamp")],
        )
        _create_table(ddb, "helio-settings", "settingId", ["settingId"])

        sns = boto3.client("sns")
        sqs = boto3.client("sqs")
        topic_arn = sns.create_topic(Name="helio-alerts")["TopicArn"]
        queue_url = sqs.create_queue(QueueName="emails")["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)
        os.environ["ALERT_TOPIC_ARN"] = topic_arn
        yield queue_url


@pytest.fixture
def sent_emails(aws):
    """Lê o que foi publicado no SNS (via fila SQS assinante). Cada chamada consome as mensagens lidas."""

    def read():
        messages = boto3.client("sqs").receive_message(QueueUrl=aws, MaxNumberOfMessages=10).get("Messages", [])
        return [json.loads(m["Body"]) for m in messages]

    return read
