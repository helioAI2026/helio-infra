import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime
from decimal import Decimal
import uuid
from dynamodb_adapter import DynamoDBAdapter
from botocore.exceptions import ClientError


@pytest.fixture
def dynamodb_adapter():
    with patch('dynamodb_adapter.boto3.resource'):
        adapter = DynamoDBAdapter("test-table", "us-east-1")
        adapter.table = MagicMock()
        return adapter


class TestDynamoDBAdapterSaveEvent:

    def test_save_event_success(self, dynamodb_adapter):
        dynamodb_adapter.table.put_item = MagicMock()
        
        result = dynamodb_adapter.save_event("driver1", "drowsiness", 0.25)
        
        assert result is not None
        assert isinstance(result, str)
        dynamodb_adapter.table.put_item.assert_called_once()
        
        call_kwargs = dynamodb_adapter.table.put_item.call_args[1]
        item = call_kwargs["Item"]
        assert item["driver_id"] == "driver1"
        assert item["event_type"] == "drowsiness"
        assert isinstance(item["ear_value"], Decimal)
        assert item["ear_value"] == Decimal("0.25")
        assert "event_id" in item
        assert "timestamp" in item

    def test_save_event_with_s3_key(self, dynamodb_adapter):
        dynamodb_adapter.table.put_item = MagicMock()
        
        dynamodb_adapter.save_event("driver1", "drowsiness", 0.25, s3_key="s3://bucket/key")
        
        call_kwargs = dynamodb_adapter.table.put_item.call_args[1]
        item = call_kwargs["Item"]
        assert item["s3_key"] == "s3://bucket/key"

    def test_save_event_with_session_id(self, dynamodb_adapter):
        dynamodb_adapter.table.put_item = MagicMock()
        
        dynamodb_adapter.save_event("driver1", "drowsiness", 0.25, session_id="session123")
        
        call_kwargs = dynamodb_adapter.table.put_item.call_args[1]
        item = call_kwargs["Item"]
        assert item["session_id"] == "session123"

    def test_save_event_with_extra_fields(self, dynamodb_adapter):
        dynamodb_adapter.table.put_item = MagicMock()
        extra = {"custom_field": "value", "score": 42}
        
        dynamodb_adapter.save_event("driver1", "drowsiness", 0.25, extra=extra)
        
        call_kwargs = dynamodb_adapter.table.put_item.call_args[1]
        item = call_kwargs["Item"]
        assert item["custom_field"] == "value"
        assert item["score"] == 42

    def test_save_event_ear_value_rounding(self, dynamodb_adapter):
        dynamodb_adapter.table.put_item = MagicMock()
        
        dynamodb_adapter.save_event("driver1", "drowsiness", 0.256789)
        
        call_kwargs = dynamodb_adapter.table.put_item.call_args[1]
        item = call_kwargs["Item"]
        assert item["ear_value"] == Decimal("0.2568")

    def test_save_event_client_error(self, dynamodb_adapter):
        error = ClientError({"Error": {"Code": "ValidationException"}}, "PutItem")
        dynamodb_adapter.table.put_item = MagicMock(side_effect=error)
        
        result = dynamodb_adapter.save_event("driver1", "drowsiness", 0.25)
        
        assert result is None


class TestDynamoDBAdapterSaveSessionSummary:

    def test_save_session_summary_success(self, dynamodb_adapter):
        dynamodb_adapter.table.put_item = MagicMock()
        
        result = dynamodb_adapter.save_session_summary("driver1", "session123", 5, 3600)
        
        assert result is True
        dynamodb_adapter.table.put_item.assert_called_once()
        
        call_kwargs = dynamodb_adapter.table.put_item.call_args[1]
        item = call_kwargs["Item"]
        assert item["driver_id"] == "driver1"
        assert item["session_id"] == "session123"
        assert item["total_events"] == 5
        assert item["duration_seconds"] == 3600
        assert item["timestamp"].startswith("session#")

    def test_save_session_summary_with_video_key(self, dynamodb_adapter):
        dynamodb_adapter.table.put_item = MagicMock()
        
        dynamodb_adapter.save_session_summary("driver1", "session123", 5, 3600, 
                                             s3_video_key="s3://bucket/video.mp4")
        
        call_kwargs = dynamodb_adapter.table.put_item.call_args[1]
        item = call_kwargs["Item"]
        assert item["s3_video_key"] == "s3://bucket/video.mp4"

    def test_save_session_summary_client_error(self, dynamodb_adapter):
        error = ClientError({"Error": {"Code": "ValidationException"}}, "PutItem")
        dynamodb_adapter.table.put_item = MagicMock(side_effect=error)
        
        result = dynamodb_adapter.save_session_summary("driver1", "session123", 5, 3600)
        
        assert result is False


class TestDynamoDBAdapterGetEventsByDriver:

    def test_get_events_by_driver_success(self, dynamodb_adapter):
        mock_response = {
            "Items": [
                {
                    "driver_id": "driver1",
                    "timestamp": "2024-01-01T12:00:00Z",
                    "event_type": "drowsiness",
                    "ear_value": Decimal("0.25"),
                    "event_id": "event1"
                }
            ]
        }
        dynamodb_adapter.table.query = MagicMock(return_value=mock_response)
        
        result = dynamodb_adapter.get_events_by_driver("driver1")
        
        assert len(result) == 1
        assert result[0]["driver_id"] == "driver1"
        assert result[0]["ear_value"] == 0.25
        dynamodb_adapter.table.query.assert_called_once()

    def test_get_events_by_driver_with_time_range(self, dynamodb_adapter):
        mock_response = {"Items": []}
        dynamodb_adapter.table.query = MagicMock(return_value=mock_response)
        
        dynamodb_adapter.get_events_by_driver("driver1", 
                                             start_time="2024-01-01T00:00:00Z",
                                             end_time="2024-01-02T00:00:00Z")
        
        dynamodb_adapter.table.query.assert_called_once()

    def test_get_events_by_driver_with_event_type_filter(self, dynamodb_adapter):
        mock_response = {"Items": []}
        dynamodb_adapter.table.query = MagicMock(return_value=mock_response)
        
        dynamodb_adapter.get_events_by_driver("driver1", event_type="drowsiness")
        
        call_kwargs = dynamodb_adapter.table.query.call_args[1]
        assert "FilterExpression" in call_kwargs

    def test_get_events_by_driver_decimal_conversion(self, dynamodb_adapter):
        mock_response = {
            "Items": [
                {
                    "ear_value": Decimal("0.1234"),
                    "driver_id": "driver1",
                    "timestamp": "2024-01-01T12:00:00Z"
                }
            ]
        }
        dynamodb_adapter.table.query = MagicMock(return_value=mock_response)
        
        result = dynamodb_adapter.get_events_by_driver("driver1")
        
        assert isinstance(result[0]["ear_value"], float)
        assert result[0]["ear_value"] == 0.1234

    def test_get_events_by_driver_client_error(self, dynamodb_adapter):
        error = ClientError({"Error": {"Code": "ValidationException"}}, "Query")
        dynamodb_adapter.table.query = MagicMock(side_effect=error)
        
        result = dynamodb_adapter.get_events_by_driver("driver1")
        
        assert result == []

    def test_get_events_by_driver_limit(self, dynamodb_adapter):
        mock_response = {"Items": []}
        dynamodb_adapter.table.query = MagicMock(return_value=mock_response)
        
        dynamodb_adapter.get_events_by_driver("driver1", limit=100)
        
        call_kwargs = dynamodb_adapter.table.query.call_args[1]
        assert call_kwargs["Limit"] == 100


class TestDynamoDBAdapterGetEvent:

    def test_get_event_success(self, dynamodb_adapter):
        mock_response = {
            "Item": {
                "driver_id": "driver1",
                "timestamp": "2024-01-01T12:00:00Z",
                "ear_value": Decimal("0.25")
            }
        }
        dynamodb_adapter.table.get_item = MagicMock(return_value=mock_response)
        
        result = dynamodb_adapter.get_event("driver1", "2024-01-01T12:00:00Z")
        
        assert result is not None
        assert result["driver_id"] == "driver1"
        assert result["ear_value"] == 0.25

    def test_get_event_not_found(self, dynamodb_adapter):
        dynamodb_adapter.table.get_item = MagicMock(return_value={})
        
        result = dynamodb_adapter.get_event("driver1", "2024-01-01T12:00:00Z")
        
        assert result is None

    def test_get_event_client_error(self, dynamodb_adapter):
        error = ClientError({"Error": {"Code": "ValidationException"}}, "GetItem")
        dynamodb_adapter.table.get_item = MagicMock(side_effect=error)
        
        result = dynamodb_adapter.get_event("driver1", "2024-01-01T12:00:00Z")
        
        assert result is None


class TestDynamoDBAdapterCountEventsInSession:

    def test_count_events_in_session_success(self, dynamodb_adapter):
        mock_response = {"Count": 5}
        dynamodb_adapter.table.query = MagicMock(return_value=mock_response)
        
        result = dynamodb_adapter.count_events_in_session("driver1", "session123")
        
        assert result == 5

    def test_count_events_in_session_zero(self, dynamodb_adapter):
        mock_response = {"Count": 0}
        dynamodb_adapter.table.query = MagicMock(return_value=mock_response)
        
        result = dynamodb_adapter.count_events_in_session("driver1", "session123")
        
        assert result == 0

    def test_count_events_in_session_client_error(self, dynamodb_adapter):
        error = ClientError({"Error": {"Code": "ValidationException"}}, "Query")
        dynamodb_adapter.table.query = MagicMock(side_effect=error)
        
        result = dynamodb_adapter.count_events_in_session("driver1", "session123")
        
        assert result == 0


class TestDynamoDBAdapterDeleteEvent:

    def test_delete_event_success(self, dynamodb_adapter):
        dynamodb_adapter.table.delete_item = MagicMock()
        
        result = dynamodb_adapter.delete_event("driver1", "2024-01-01T12:00:00Z")
        
        assert result is True
        dynamodb_adapter.table.delete_item.assert_called_once()

    def test_delete_event_client_error(self, dynamodb_adapter):
        error = ClientError({"Error": {"Code": "ValidationException"}}, "DeleteItem")
        dynamodb_adapter.table.delete_item = MagicMock(side_effect=error)
        
        result = dynamodb_adapter.delete_event("driver1", "2024-01-01T12:00:00Z")
        
        assert result is False


class TestDynamoDBAdapterDeserialization:

    def test_deserialize_decimal_values(self, dynamodb_adapter):
        item = {
            "driver_id": "driver1",
            "ear_value": Decimal("0.5"),
            "score": Decimal("100"),
            "message": "test"
        }
        
        result = dynamodb_adapter._deserialize_item(item)
        
        assert isinstance(result["ear_value"], float)
        assert isinstance(result["score"], float)
        assert isinstance(result["message"], str)
        assert result["ear_value"] == 0.5
        assert result["score"] == 100.0

    def test_deserialize_items_list(self, dynamodb_adapter):
        items = [
            {"value": Decimal("0.1"), "text": "a"},
            {"value": Decimal("0.2"), "text": "b"}
        ]
        
        result = dynamodb_adapter._deserialize_items(items)
        
        assert len(result) == 2
        assert result[0]["value"] == 0.1
        assert result[1]["value"] == 0.2
