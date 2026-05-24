# Testing Guide

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run all tests:
```bash
pytest
```

3. Run tests with coverage:
```bash
pytest --cov=. --cov-report=html
```

4. Run specific test file:
```bash
pytest tests/test_s3_adapter.py
pytest tests/test_dynamodb_adapter.py
```

5. Run specific test class:
```bash
pytest tests/test_s3_adapter.py::TestS3AdapterUpload
```

6. Run specific test:
```bash
pytest tests/test_s3_adapter.py::TestS3AdapterUpload::test_upload_snapshot_success
```

## Test Coverage

### S3Adapter Tests
- **Upload operations**: snapshot, video, bytes uploads
- **Download operations**: file downloads
- **Presigned URLs**: URL generation with custom expiration
- **List operations**: event listing with filters
- **Error handling**: ClientError and FileNotFoundError

### DynamoDBAdapter Tests  
- **Write operations**: save events, session summaries
- **Read operations**: get events by driver, get single event, count events
- **Delete operations**: remove events
- **Data conversion**: Decimal to float conversion
- **Error handling**: ClientError handling

## Running Tests in CI/CD

```bash
pytest --cov=. --cov-report=term-missing --junitxml=test-results.xml
```
