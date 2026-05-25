import pytest
import json
import base64
from unittest.mock import Mock, MagicMock, patch
from handler import lambda_handler, EventProcessor


@pytest.fixture
def event_processor():
    with patch('handler.S3Adapter') as mock_s3, \
         patch('handler.DynamoDBAdapter') as mock_db:
        mock_s3_instance = MagicMock()
        mock_db_instance = MagicMock()
        mock_s3.return_value = mock_s3_instance
        mock_db.return_value = mock_db_instance
        
        processor = EventProcessor()
        processor.s3_adapter = mock_s3_instance
        processor.dynamodb_adapter = mock_db_instance
        return processor


class TestEventProcessorDrowsinessEvent:

    def test_process_drowsiness_event_success(self, event_processor):
        event_processor.dynamodb_adapter.save_event = MagicMock(return_value="event_id_123")
        
        event_data = {
            "action": "save_event",
            "driver_id": "driver123",
            "event_type": "drowsiness",
            "ear_value": 0.25
        }
        
        result = event_processor.process_drowsiness_event(event_data)
        
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["event_id"] == "event_id_123"
        assert body["driver_id"] == "driver123"
        assert "message" in body
        
        event_processor.dynamodb_adapter.save_event.assert_called_once_with(
            driver_id="driver123",
            event_type="drowsiness",
            ear_value=0.25,
            s3_key=None,
            session_id=None
        )

    def test_process_drowsiness_event_with_snapshot(self, event_processor):
        event_processor.s3_adapter.upload_bytes = MagicMock(return_value="s3://bucket/key")
        event_processor.dynamodb_adapter.save_event = MagicMock(return_value="event_id_456")
        
        snapshot_data = b"fake image data"
        snapshot_base64 = base64.b64encode(snapshot_data).decode('utf-8')
        
        event_data = {
            "action": "save_event",
            "driver_id": "driver456",
            "event_type": "drowsiness",
            "ear_value": 0.30,
            "snapshot_base64": snapshot_base64,
            "session_id": "session_001"
        }
        
        result = event_processor.process_drowsiness_event(event_data)
        
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["event_id"] == "event_id_456"
        assert body["s3_key"] == "s3://bucket/key"
        
        event_processor.s3_adapter.upload_bytes.assert_called_once()
        event_processor.dynamodb_adapter.save_event.assert_called_once()

    def test_process_drowsiness_event_missing_driver_id(self, event_processor):
        event_data = {
            "action": "save_event",
            "event_type": "drowsiness",
            "ear_value": 0.25
        }
        
        result = event_processor.process_drowsiness_event(event_data)
        
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "driver_id" in body["error"]

    def test_process_drowsiness_event_invalid_ear_value(self, event_processor):
        event_data = {
            "action": "save_event",
            "driver_id": "driver123",
            "event_type": "drowsiness",
            "ear_value": 1.5
        }
        
        result = event_processor.process_drowsiness_event(event_data)
        
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "ear_value" in body["error"]

    def test_process_drowsiness_event_dynamodb_failure(self, event_processor):
        event_processor.dynamodb_adapter.save_event = MagicMock(return_value=None)
        
        event_data = {
            "action": "save_event",
            "driver_id": "driver123",
            "event_type": "drowsiness",
            "ear_value": 0.25
        }
        
        result = event_processor.process_drowsiness_event(event_data)
        
        assert result["statusCode"] == 500
        body = json.loads(result["body"])
        assert "DynamoDB" in body["error"]


class TestEventProcessorSessionSummary:

    def test_process_session_summary_success(self, event_processor):
        event_processor.dynamodb_adapter.save_session_summary = MagicMock(return_value=True)
        
        event_data = {
            "action": "save_session_summary",
            "driver_id": "driver123",
            "session_id": "session_001",
            "total_events": 5,
            "duration_seconds": 1800
        }
        
        result = event_processor.process_session_summary(event_data)
        
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["driver_id"] == "driver123"
        assert body["session_id"] == "session_001"
        assert body["total_events"] == 5

    def test_process_session_summary_with_video(self, event_processor):
        event_processor.s3_adapter.upload_bytes = MagicMock(return_value="s3://bucket/video.mp4")
        event_processor.dynamodb_adapter.save_session_summary = MagicMock(return_value=True)
        
        video_data = b"fake video data"
        video_base64 = base64.b64encode(video_data).decode('utf-8')
        
        event_data = {
            "action": "save_session_summary",
            "driver_id": "driver123",
            "session_id": "session_001",
            "total_events": 5,
            "duration_seconds": 1800,
            "video_base64": video_base64
        }
        
        result = event_processor.process_session_summary(event_data)
        
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["s3_video_key"] == "s3://bucket/video.mp4"

    def test_process_session_summary_missing_fields(self, event_processor):
        event_data = {
            "action": "save_session_summary",
            "driver_id": "driver123"
        }
        
        result = event_processor.process_session_summary(event_data)
        
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "driver_id" in body["error"] or "session_id" in body["error"]


class TestEventProcessorGetEvents:

    def test_get_driver_events_success(self, event_processor):
        mock_events = [
            {"event_id": "evt1", "ear_value": 0.25},
            {"event_id": "evt2", "ear_value": 0.30}
        ]
        event_processor.dynamodb_adapter.get_events_by_driver = MagicMock(return_value=mock_events)
        
        event_data = {
            "action": "get_events",
            "driver_id": "driver123",
            "limit": 50
        }
        
        result = event_processor.get_driver_events(event_data)
        
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["total_events"] == 2
        assert len(body["events"]) == 2

    def test_get_driver_events_with_filter(self, event_processor):
        mock_events = [{"event_id": "evt1", "ear_value": 0.25}]
        event_processor.dynamodb_adapter.get_events_by_driver = MagicMock(return_value=mock_events)
        
        event_data = {
            "action": "get_events",
            "driver_id": "driver123",
            "event_type": "drowsiness",
            "limit": 10
        }
        
        result = event_processor.get_driver_events(event_data)
        
        assert result["statusCode"] == 200
        event_processor.dynamodb_adapter.get_events_by_driver.assert_called_once_with(
            driver_id="driver123",
            event_type="drowsiness",
            limit=10
        )

    def test_get_driver_events_missing_driver_id(self, event_processor):
        event_data = {
            "action": "get_events",
            "limit": 50
        }
        
        result = event_processor.get_driver_events(event_data)
        
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "driver_id" in body["error"]


class TestEventProcessorPresignedUrl:

    def test_get_presigned_url_success(self, event_processor):
        test_url = "https://s3.amazonaws.com/bucket/key?signature"
        event_processor.s3_adapter.generate_presigned_url = MagicMock(return_value=test_url)
        
        event_data = {
            "action": "get_presigned_url",
            "s3_key": "snapshots/driver123/drowsiness/image.jpg",
            "expiration_seconds": 7200
        }
        
        result = event_processor.get_presigned_url(event_data)
        
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["presigned_url"] == test_url
        assert body["expiration_seconds"] == 7200

    def test_get_presigned_url_missing_key(self, event_processor):
        event_data = {
            "action": "get_presigned_url"
        }
        
        result = event_processor.get_presigned_url(event_data)
        
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "s3_key" in body["error"]


class TestLambdaHandler:

    def test_lambda_handler_save_event(self):
        with patch('handler.EventProcessor') as mock_processor_class:
            mock_processor = MagicMock()
            mock_processor_class.return_value = mock_processor
            mock_processor.process_drowsiness_event = MagicMock(
                return_value={"statusCode": 200, "body": json.dumps({"event_id": "123"})}
            )
            
            event = {
                "action": "save_event",
                "driver_id": "driver123",
                "event_type": "drowsiness",
                "ear_value": 0.25
            }
            
            result = lambda_handler(event, None)
            
            assert result["statusCode"] == 200
            mock_processor.process_drowsiness_event.assert_called_once()

    def test_lambda_handler_with_api_gateway_format(self):
        with patch('handler.EventProcessor') as mock_processor_class:
            mock_processor = MagicMock()
            mock_processor_class.return_value = mock_processor
            mock_processor.process_drowsiness_event = MagicMock(
                return_value={"statusCode": 200, "body": json.dumps({"event_id": "123"})}
            )
            
            event_body = {
                "action": "save_event",
                "driver_id": "driver123",
                "event_type": "drowsiness",
                "ear_value": 0.25
            }
            event = {
                "body": json.dumps(event_body)
            }
            
            result = lambda_handler(event, None)
            
            assert result["statusCode"] == 200

    def test_lambda_handler_invalid_action(self):
        event = {
            "action": "invalid_action",
            "driver_id": "driver123"
        }
        
        with patch('handler.EventProcessor') as mock_processor_class:
            mock_processor = MagicMock()
            mock_processor_class.return_value = mock_processor
            mock_processor._error_response = MagicMock(
                return_value={"statusCode": 400, "body": json.dumps({"error": "Invalid action"})}
            )
            
            result = lambda_handler(event, None)
            
            assert result["statusCode"] == 400

    def test_lambda_handler_json_decode_error(self):
        event = {
            "body": "invalid json{"
        }
        
        result = lambda_handler(event, None)
        
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "JSON" in body["error"]

    def test_lambda_handler_all_actions(self):
        with patch('handler.EventProcessor') as mock_processor_class:
            mock_processor = MagicMock()
            mock_processor_class.return_value = mock_processor
            
            success_response = {"statusCode": 200, "body": json.dumps({"success": True})}
            mock_processor.process_drowsiness_event.return_value = success_response
            mock_processor.process_session_summary.return_value = success_response
            mock_processor.get_driver_events.return_value = success_response
            mock_processor.get_presigned_url.return_value = success_response
            
            actions = ["save_event", "save_session_summary", "get_events", "get_presigned_url"]
            
            for action in actions:
                event = {"action": action}
                result = lambda_handler(event, None)
                assert result["statusCode"] == 200
