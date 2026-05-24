import boto3
import logging
import uuid
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


class DynamoDBAdapter:

    def __init__(self, table_name: str, region: str = "us-east-1"):
        self.table_name = table_name
        self.region = region
        resource = boto3.resource("dynamodb", region_name=region)
        self.table = resource.Table(table_name)

    def save_event(
        self,
        driver_id: str,
        event_type: str,
        ear_value: float,
        s3_key: str | None = None,
        session_id: str | None = None,
        extra: dict | None = None,
    ) -> str | None:
        event_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat() + "Z"

        item: dict[str, Any] = {
            "driver_id": driver_id,
            "timestamp": timestamp,
            "event_id": event_id,
            "event_type": event_type,
            "ear_value": Decimal(str(round(ear_value, 4))),
        }

        if s3_key:
            item["s3_key"] = s3_key
        if session_id:
            item["session_id"] = session_id
        if extra:
            item.update({k: v for k, v in extra.items() if k not in item})

        try:
            self.table.put_item(Item=item)
            logger.info(
                "Evento salvo: driver=%s type=%s event_id=%s",
                driver_id, event_type, event_id,
            )
            return event_id
        except ClientError as e:
            logger.error("Erro ao salvar evento no DynamoDB: %s", e)
            return None

    def save_session_summary(
        self,
        driver_id: str,
        session_id: str,
        total_events: int,
        duration_seconds: int,
        s3_video_key: str | None = None,
    ) -> bool:
        timestamp = datetime.now(timezone.utc).isoformat() + "Z"

        item = {
            "driver_id": driver_id,
            "timestamp": f"session#{timestamp}",
            "session_id": session_id,
            "total_events": total_events,
            "duration_seconds": duration_seconds,
        }
        if s3_video_key:
            item["s3_video_key"] = s3_video_key

        try:
            self.table.put_item(Item=item)
            logger.info("Resumo de sessão salvo: session_id=%s", session_id)
            return True
        except ClientError as e:
            logger.error("Erro ao salvar resumo de sessão: %s", e)
            return False

    def get_events_by_driver(
        self,
        driver_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        try:
            key_condition = Key("driver_id").eq(driver_id)

            if start_time and end_time:
                key_condition &= Key("timestamp").between(start_time, end_time)
            elif start_time:
                key_condition &= Key("timestamp").gte(start_time)
            elif end_time:
                key_condition &= Key("timestamp").lte(end_time)

            kwargs: dict[str, Any] = {
                "KeyConditionExpression": key_condition,
                "Limit": limit,
                "ScanIndexForward": False,
            }

            if event_type:
                kwargs["FilterExpression"] = Attr("event_type").eq(event_type)

            response = self.table.query(**kwargs)
            return self._deserialize_items(response.get("Items", []))

        except ClientError as e:
            logger.error("Erro ao consultar eventos no DynamoDB: %s", e)
            return []

    def get_event(self, driver_id: str, timestamp: str) -> dict | None:
        try:
            response = self.table.get_item(
                Key={"driver_id": driver_id, "timestamp": timestamp}
            )
            item = response.get("Item")
            return self._deserialize_item(item) if item else None
        except ClientError as e:
            logger.error("Erro ao buscar evento no DynamoDB: %s", e)
            return None

    def count_events_in_session(self, driver_id: str, session_id: str) -> int:
        try:
            response = self.table.query(
                KeyConditionExpression=Key("driver_id").eq(driver_id),
                FilterExpression=Attr("session_id").eq(session_id),
                Select="COUNT",
            )
            return response.get("Count", 0)
        except ClientError as e:
            logger.error("Erro ao contar eventos da sessão: %s", e)
            return 0

    def delete_event(self, driver_id: str, timestamp: str) -> bool:
        try:
            self.table.delete_item(
                Key={"driver_id": driver_id, "timestamp": timestamp}
            )
            logger.info("Evento removido: driver=%s timestamp=%s", driver_id, timestamp)
            return True
        except ClientError as e:
            logger.error("Erro ao remover evento: %s", e)
            return False

    def _deserialize_items(self, items: list[dict]) -> list[dict]:
        return [self._deserialize_item(item) for item in items]

    def _deserialize_item(self, item: dict) -> dict:
        result = {}
        for k, v in item.items():
            if isinstance(v, Decimal):
                result[k] = float(v)
            else:
                result[k] = v
        return result
