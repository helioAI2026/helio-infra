import pytest
from unittest.mock import Mock, MagicMock, patch, call
from datetime import datetime, timezone
from decimal import Decimal
from s3_adapter import S3Adapter


@pytest.fixture
def s3_adapter():
    with patch('s3_adapter.boto3.client'):
        adapter = S3Adapter("test-bucket")
        adapter.s3_client = MagicMock()
        return adapter


class TestS3AdapterUpload:

    def test_upload_snapshot_success(self, s3_adapter):
        s3_adapter._upload_file = MagicMock(return_value="test_key")
        
        result = s3_adapter.upload_snapshot("/path/to/image.jpg", "driver123", "drowsiness")
        
        assert result == "test_key"
        s3_adapter._upload_file.assert_called_once()
        args = s3_adapter._upload_file.call_args
        assert "snapshots/driver123/drowsiness/" in args[0][1]
        assert args[1]["content_type"] == "image/jpeg"

    def test_upload_video_success(self, s3_adapter):
        s3_adapter._upload_file = MagicMock(return_value="test_key")
        
        result = s3_adapter.upload_video("/path/to/video.mp4", "driver456", "session789")
        
        assert result == "test_key"
        s3_adapter._upload_file.assert_called_once()
        args = s3_adapter._upload_file.call_args
        assert "videos/driver456/session789/" in args[0][1]
        assert args[1]["content_type"] == "video/mp4"

    def test_upload_bytes_success(self, s3_adapter):
        test_data = b"test content"
        s3_adapter.s3_client.put_object = MagicMock()
        
        result = s3_adapter.upload_bytes(test_data, "test/key.bin")
        
        assert result == "test/key.bin"
        s3_adapter.s3_client.put_object.assert_called_once()
        call_kwargs = s3_adapter.s3_client.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["Key"] == "test/key.bin"
        assert call_kwargs["Body"] == test_data

    def test_upload_bytes_with_custom_content_type(self, s3_adapter):
        test_data = b"test"
        s3_adapter.s3_client.put_object = MagicMock()
        
        s3_adapter.upload_bytes(test_data, "key", "text/plain")
        
        call_kwargs = s3_adapter.s3_client.put_object.call_args[1]
        assert call_kwargs["ContentType"] == "text/plain"

    def test_upload_bytes_client_error(self, s3_adapter):
        from botocore.exceptions import ClientError
        test_data = b"test"
        error = ClientError({"Error": {"Code": "NoSuchBucket"}}, "PutObject")
        s3_adapter.s3_client.put_object = MagicMock(side_effect=error)
        
        result = s3_adapter.upload_bytes(test_data, "key")
        
        assert result is None


class TestS3AdapterDownload:

    def test_download_file_success(self, s3_adapter):
        s3_adapter.s3_client.download_file = MagicMock()
        
        with patch('s3_adapter.os.makedirs'):
            result = s3_adapter.download_file("test/key.bin", "/local/path.bin")
        
        assert result is True
        s3_adapter.s3_client.download_file.assert_called_once()

    def test_download_file_client_error(self, s3_adapter):
        from botocore.exceptions import ClientError
        error = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        s3_adapter.s3_client.download_file = MagicMock(side_effect=error)
        
        with patch('s3_adapter.os.makedirs'):
            result = s3_adapter.download_file("test/key.bin", "/local/path.bin")
        
        assert result is False


class TestS3AdapterPresigned:

    def test_generate_presigned_url_success(self, s3_adapter):
        test_url = "https://test-bucket.s3.amazonaws.com/test/key?signature"
        s3_adapter.s3_client.generate_presigned_url = MagicMock(return_value=test_url)
        
        result = s3_adapter.generate_presigned_url("test/key.bin")
        
        assert result == test_url
        s3_adapter.s3_client.generate_presigned_url.assert_called_once()
        args = s3_adapter.s3_client.generate_presigned_url.call_args
        assert args[0][0] == "get_object"
        assert args[1]["ExpiresIn"] == 3600

    def test_generate_presigned_url_custom_expiration(self, s3_adapter):
        test_url = "https://test-bucket.s3.amazonaws.com/test/key"
        s3_adapter.s3_client.generate_presigned_url = MagicMock(return_value=test_url)
        
        s3_adapter.generate_presigned_url("test/key", 7200)
        
        args = s3_adapter.s3_client.generate_presigned_url.call_args
        assert args[1]["ExpiresIn"] == 7200

    def test_generate_presigned_url_client_error(self, s3_adapter):
        from botocore.exceptions import ClientError
        error = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        s3_adapter.s3_client.generate_presigned_url = MagicMock(side_effect=error)
        
        result = s3_adapter.generate_presigned_url("test/key")
        
        assert result is None


class TestS3AdapterList:

    def test_list_events_success(self, s3_adapter):
        mock_paginator = MagicMock()
        mock_paginator.paginate = MagicMock(return_value=[
            {
                "Contents": [
                    {
                        "Key": "snapshots/driver1/drowsiness/20240101T120000Z_image.jpg",
                        "Size": 5000,
                        "LastModified": datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
                    }
                ]
            }
        ])
        s3_adapter.s3_client.get_paginator = MagicMock(return_value=mock_paginator)
        
        result = s3_adapter.list_events("driver1", "drowsiness")
        
        assert len(result) == 1
        assert result[0]["key"] == "snapshots/driver1/drowsiness/20240101T120000Z_image.jpg"
        assert result[0]["size"] == 5000

    def test_list_events_no_event_type_filter(self, s3_adapter):
        mock_paginator = MagicMock()
        mock_paginator.paginate = MagicMock(return_value=[{"Contents": []}])
        s3_adapter.s3_client.get_paginator = MagicMock(return_value=mock_paginator)
        
        s3_adapter.list_events("driver1")
        
        paginate_args = mock_paginator.paginate.call_args[1]
        assert paginate_args["Prefix"] == "snapshots/driver1/"

    def test_list_events_client_error(self, s3_adapter):
        from botocore.exceptions import ClientError
        error = ClientError({"Error": {"Code": "AccessDenied"}}, "ListObjectsV2")
        s3_adapter.s3_client.get_paginator = MagicMock(side_effect=error)
        
        result = s3_adapter.list_events("driver1")
        
        assert result == []


class TestS3AdapterUploadFile:

    def test_upload_file_success(self, s3_adapter):
        s3_adapter.s3_client.upload_file = MagicMock()
        
        result = s3_adapter._upload_file("/path/image.jpg", "snapshots/driver1/test.jpg", "image/jpeg")
        
        assert result == "snapshots/driver1/test.jpg"
        s3_adapter.s3_client.upload_file.assert_called_once()

    def test_upload_file_not_found(self, s3_adapter):
        s3_adapter.s3_client.upload_file = MagicMock(side_effect=FileNotFoundError())
        
        result = s3_adapter._upload_file("/nonexistent/file.jpg", "key", "image/jpeg")
        
        assert result is None

    def test_upload_file_client_error(self, s3_adapter):
        from botocore.exceptions import ClientError
        error = ClientError({"Error": {"Code": "NoSuchBucket"}}, "PutObject")
        s3_adapter.s3_client.upload_file = MagicMock(side_effect=error)
        
        result = s3_adapter._upload_file("/path/file.jpg", "key", "image/jpeg")
        
        assert result is None
