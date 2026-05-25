import pytest
import os
import boto3
from datetime import datetime, timezone
from decimal import Decimal
from s3_adapter import S3Adapter
from dynamodb_adapter import DynamoDBAdapter
from botocore.exceptions import ClientError


@pytest.fixture(scope="session")
def aws_config():
    config = {
        "s3_bucket": os.getenv("S3_BUCKET", "helio-ai-events"),
        "dynamodb_table": os.getenv("DYNAMODB_TABLE", "HelioDriveEvents"),
        "region": os.getenv("AWS_REGION", "us-east-1")
    }
    return config


@pytest.fixture
def s3_adapter(aws_config):
    return S3Adapter(aws_config["s3_bucket"])


@pytest.fixture
def dynamodb_adapter(aws_config):
    return DynamoDBAdapter(aws_config["dynamodb_table"], aws_config["region"])


@pytest.fixture
def test_file(tmp_path):
    file_path = tmp_path / "test_image.jpg"
    file_path.write_bytes(b"fake image data for testing")
    return str(file_path)


class TestS3AdapterIntegration:

    def test_upload_bytes_to_s3(self, s3_adapter):
        test_key = "integration_test/bytes_test_data.bin"
        test_data = b"Test data for S3 upload"
        
        result = s3_adapter.upload_bytes(test_data, test_key)
        assert result == test_key
        
        # Verify by listing objects
        events = s3_adapter.list_events("integration_test")
        assert len(events) > 0
        assert any(event["key"] == test_key for event in events)

    def test_upload_snapshot_to_s3(self, s3_adapter, test_file):
        driver_id = "test_driver_001"
        event_type = "drowsiness"
        
        result = s3_adapter.upload_snapshot(test_file, driver_id, event_type)
        
        assert result is not None
        assert f"snapshots/{driver_id}/{event_type}/" in result

    def test_upload_video_to_s3(self, s3_adapter, test_file):
        driver_id = "test_driver_001"
        session_id = "session_20240524_120000"
        
        result = s3_adapter.upload_video(test_file, driver_id, session_id)
        
        assert result is not None
        assert f"videos/{driver_id}/{session_id}/" in result

    def test_list_events_with_filter(self, s3_adapter):
        events = s3_adapter.list_events("test_driver_001", "drowsiness")
        
        assert isinstance(events, list)
        assert all("key" in event for event in events)
        assert all("size" in event for event in events)
        assert all("last_modified" in event for event in events)

    def test_generate_presigned_url(self, s3_adapter):
        test_key = "integration_test/test_file.bin"
        s3_adapter.upload_bytes(b"test data", test_key)
        
        url = s3_adapter.generate_presigned_url(test_key, 3600)
        
        assert url is not None
        assert s3_adapter.bucket_name in url
        assert test_key in url
        assert "Signature" in url or "X-Amz" in url


class TestDynamoDBAdapterIntegration:

    def test_save_and_retrieve_event(self, dynamodb_adapter):
        driver_id = "integration_test_driver"
        event_type = "drowsiness"
        ear_value = 0.23
        
        event_id = dynamodb_adapter.save_event(driver_id, event_type, ear_value)
        
        assert event_id is not None
        
        # Retrieve and verify
        timestamp = datetime.now(timezone.utc).isoformat() + "Z"
        events = dynamodb_adapter.get_events_by_driver(driver_id, limit=10)
        
        assert len(events) > 0
        assert any(event["event_id"] == event_id for event in events)

    def test_save_event_with_session(self, dynamodb_adapter):
        driver_id = "integration_test_driver"
        session_id = "session_integration_001"
        
        event_id = dynamodb_adapter.save_event(
            driver_id,
            "drowsiness",
            0.25,
            session_id=session_id
        )
        
        assert event_id is not None
        events = dynamodb_adapter.get_events_by_driver(driver_id, limit=10)
        assert any(e["session_id"] == session_id for e in events if "session_id" in e)

    def test_save_event_with_s3_key(self, dynamodb_adapter):
        driver_id = "integration_test_driver"
        s3_key = "snapshots/test/20240524T120000Z_image.jpg"
        
        event_id = dynamodb_adapter.save_event(
            driver_id,
            "drowsiness",
            0.22,
            s3_key=s3_key
        )
        
        assert event_id is not None
        events = dynamodb_adapter.get_events_by_driver(driver_id, limit=10)
        assert any(e["s3_key"] == s3_key for e in events if "s3_key" in e)

    def test_save_session_summary(self, dynamodb_adapter):
        driver_id = "integration_test_driver"
        session_id = "session_integration_summary_001"
        s3_video_key = "videos/test/20240524T120000Z_video.mp4"
        
        result = dynamodb_adapter.save_session_summary(
            driver_id,
            session_id,
            total_events=5,
            duration_seconds=3600,
            s3_video_key=s3_video_key
        )
        
        assert result is True

    def test_get_events_by_driver(self, dynamodb_adapter):
        driver_id = "integration_test_driver"
        
        events = dynamodb_adapter.get_events_by_driver(driver_id, limit=10)
        
        assert isinstance(events, list)
        for event in events:
            assert "driver_id" in event
            assert "timestamp" in event
            assert event["driver_id"] == driver_id

    def test_get_events_by_type_filter(self, dynamodb_adapter):
        driver_id = "integration_test_driver"
        
        # Save different event types
        dynamodb_adapter.save_event(driver_id, "drowsiness", 0.20)
        dynamodb_adapter.save_event(driver_id, "distraction", 0.15)
        
        events = dynamodb_adapter.get_events_by_driver(
            driver_id,
            event_type="drowsiness",
            limit=10
        )
        
        assert all(e["event_type"] == "drowsiness" for e in events)

    def test_count_events_in_session(self, dynamodb_adapter):
        driver_id = "integration_test_driver"
        session_id = "session_count_test"
        
        # Save multiple events in the same session
        for i in range(3):
            dynamodb_adapter.save_event(
                driver_id,
                "drowsiness",
                0.20 + i * 0.01,
                session_id=session_id
            )
        
        count = dynamodb_adapter.count_events_in_session(driver_id, session_id)
        
        assert count >= 3

    def test_delete_event(self, dynamodb_adapter):
        driver_id = "integration_test_driver"
        
        event_id = dynamodb_adapter.save_event(driver_id, "drowsiness", 0.25)
        
        # Get the timestamp of the saved event
        events = dynamodb_adapter.get_events_by_driver(driver_id, limit=10)
        event = next((e for e in events if e["event_id"] == event_id), None)
        
        if event:
            timestamp = event["timestamp"]
            result = dynamodb_adapter.delete_event(driver_id, timestamp)
            assert result is True

    def test_decimal_conversion(self, dynamodb_adapter):
        driver_id = "integration_test_driver"
        ear_value = 0.23456
        
        event_id = dynamodb_adapter.save_event(driver_id, "drowsiness", ear_value)
        
        events = dynamodb_adapter.get_events_by_driver(driver_id, limit=10)
        event = next((e for e in events if e["event_id"] == event_id), None)
        
        assert event is not None
        assert isinstance(event["ear_value"], float)
        assert abs(event["ear_value"] - round(ear_value, 4)) < 0.0001


class TestEndToEndIntegration:

    def test_complete_workflow(self, s3_adapter, dynamodb_adapter, test_file):
        driver_id = "e2e_test_driver"
        session_id = "e2e_session_001"
        
        # Step 1: Upload snapshot to S3
        s3_key = s3_adapter.upload_snapshot(test_file, driver_id, "drowsiness")
        assert s3_key is not None
        
        # Step 2: Save event metadata to DynamoDB with S3 reference
        event_id = dynamodb_adapter.save_event(
            driver_id,
            "drowsiness",
            0.21,
            s3_key=s3_key,
            session_id=session_id,
            extra={"confidence": 0.95}
        )
        assert event_id is not None
        
        # Step 3: Generate presigned URL for the uploaded snapshot
        presigned_url = s3_adapter.generate_presigned_url(s3_key)
        assert presigned_url is not None
        
        # Step 4: Save session summary
        summary_result = dynamodb_adapter.save_session_summary(
            driver_id,
            session_id,
            total_events=1,
            duration_seconds=300,
            s3_video_key=None
        )
        assert summary_result is True
        
        # Step 5: Retrieve and verify all data
        events = dynamodb_adapter.get_events_by_driver(driver_id)
        assert len(events) > 0
        
        event = next((e for e in events if e["event_id"] == event_id), None)
        assert event is not None
        assert event["s3_key"] == s3_key
        assert event["session_id"] == session_id
