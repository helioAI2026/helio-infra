# AWS Integration Testing Guide

## Prerequisites

1. **AWS Account**: You need an active AWS account with appropriate permissions
2. **AWS CLI**: Optional but recommended for setup verification
3. **Credentials**: AWS credentials configured as environment variables

## Setting Up AWS Credentials

### Option 1: Environment Variables (Recommended for Testing)

```bash
export AWS_ACCESS_KEY_ID="your_access_key"
export AWS_SECRET_ACCESS_KEY="your_secret_key"
export AWS_REGION="us-east-1"
```

Or create a `.env` file:
```bash
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1
```

### Option 2: AWS CLI Profile

```bash
aws configure --profile helio-ai
```

Then set in your shell:
```bash
export AWS_PROFILE=helio-ai
```

### Option 3: Verify Credentials

Test your AWS credentials:
```bash
aws sts get-caller-identity
```

Should output your Account ID and ARN.

## Creating AWS Resources

### Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 2: Run Setup Script

```bash
python setup_aws.py
```

This will:
- Verify AWS credentials
- Create S3 bucket: `helio-ai-events`
- Create DynamoDB table: `HelioDriveEvents`
- Display the resources created

### Example Output

```
============================================================
AWS Resources Setup for helioAI Event Processor
============================================================

1. Verifying AWS credentials...
✓ AWS credentials verified
  Account ID: 123456789012
  User ARN: arn:aws:iam::123456789012:user/your-user

2. Creating S3 bucket (helio-ai-events)...
✓ S3 bucket 'helio-ai-events' created successfully

3. Creating DynamoDB table (HelioDriveEvents)...
✓ DynamoDB table 'HelioDriveEvents' created successfully
  Waiting for table to be ready...
  Table is ready!

============================================================
Setup complete! Resources are ready for testing:
  S3 Bucket: helio-ai-events
  DynamoDB Table: HelioDriveEvents
  Region: us-east-1
============================================================
```

## Running Integration Tests

### All Integration Tests

```bash
pytest tests/test_integration.py -v
```

### Specific Test Class

```bash
pytest tests/test_integration.py::TestS3AdapterIntegration -v
pytest tests/test_integration.py::TestDynamoDBAdapterIntegration -v
pytest tests/test_integration.py::TestEndToEndIntegration -v
```

### Specific Test with Output

```bash
pytest tests/test_integration.py::TestS3AdapterIntegration::test_upload_bytes_to_s3 -v -s
```

### With Coverage Report

```bash
pytest tests/test_integration.py --cov=. --cov-report=html
```

## Custom AWS Resources

To use different S3 bucket or DynamoDB table names, set environment variables:

```bash
export S3_BUCKET="my-custom-bucket"
export DYNAMODB_TABLE="MyCustomTable"
export AWS_REGION="eu-west-1"

python setup_aws.py
pytest tests/test_integration.py -v
```

## Running All Tests (Unit + Integration)

```bash
# Unit tests only
pytest tests/test_s3_adapter.py tests/test_dynamodb_adapter.py -v

# Integration tests only
pytest tests/test_integration.py -v

# All tests
pytest -v

# All tests with coverage
pytest --cov=. --cov-report=term-missing
```

## Test Categories

### Unit Tests (Mocked AWS)
- File: `test_s3_adapter.py`
- File: `test_dynamodb_adapter.py`
- Uses: Mock objects for AWS services
- Speed: Fast (~0.2s)
- Cost: $0

### Integration Tests (Real AWS)
- File: `test_integration.py`
- Uses: Real AWS S3 and DynamoDB
- Speed: Slower (~5-10s depending on AWS latency)
- Cost: Minimal (AWS free tier may cover)

#### Test Classes:

1. **TestS3AdapterIntegration**
   - `test_upload_bytes_to_s3`: Upload and verify binary data
   - `test_upload_snapshot_to_s3`: Upload image files
   - `test_upload_video_to_s3`: Upload video files
   - `test_list_events_with_filter`: List and filter objects
   - `test_generate_presigned_url`: Generate signed URLs

2. **TestDynamoDBAdapterIntegration**
   - `test_save_and_retrieve_event`: Save events and query
   - `test_save_event_with_session`: Save events with session tracking
   - `test_save_event_with_s3_key`: Link events to S3 files
   - `test_save_session_summary`: Save session metadata
   - `test_get_events_by_driver`: Query driver events
   - `test_get_events_by_type_filter`: Filter by event type
   - `test_count_events_in_session`: Count session events
   - `test_delete_event`: Delete events from table
   - `test_decimal_conversion`: Verify number conversions

3. **TestEndToEndIntegration**
   - `test_complete_workflow`: Full workflow from upload to retrieval

## Cleaning Up AWS Resources

### Option 1: Interactive Cleanup

```bash
python cleanup_aws.py
```

You'll be prompted to confirm before deletion:
```
============================================================
AWS Resources Cleanup for helioAI Event Processor
============================================================

This will delete:
  - S3 bucket: helio-ai-events
  - DynamoDB table: HelioDriveEvents

Are you sure? (yes/no): yes
```

### Option 2: Manual Cleanup

**Using AWS CLI:**

```bash
# Delete S3 bucket and contents
aws s3 rb s3://helio-ai-events --force

# Delete DynamoDB table
aws dynamodb delete-table --table-name HelioDriveEvents
```

**Using AWS Console:**
1. Go to S3 → select bucket → Delete
2. Go to DynamoDB → select table → Delete

## Cost Considerations

### AWS Free Tier Coverage

- **S3**: 5GB storage, 20k GET requests monthly
- **DynamoDB**: 25 GB storage, 25 read/write capacity units monthly

For integration testing, costs should be negligible if:
- Tests complete in a few seconds
- Total data uploaded < 1 GB
- Tests don't run continuously

### Estimated Monthly Cost (If not in free tier)

With moderate testing (10 test runs, ~100 objects uploaded):
- **S3**: ~$0.10-0.50/month
- **DynamoDB**: ~$0.10-0.25/month
- **Total**: <$1/month

## Troubleshooting

### "InvalidAccessKeyId" or "InvalidSignature"

```bash
export AWS_ACCESS_KEY_ID="correct_key"
export AWS_SECRET_ACCESS_KEY="correct_secret"
```

### "BucketAlreadyOwnedByYou"

S3 bucket exists and is owned by your account. Re-run `setup_aws.py` to continue.

### "ResourceInUseException" (DynamoDB)

DynamoDB table already exists. Wait 30 seconds and re-run setup, or use cleanup_aws.py first.

### Timeouts During Integration Tests

Increase pytest timeout:
```bash
pytest tests/test_integration.py --timeout=30
```

### "AccessDenied" Errors

Verify IAM permissions for:
- `s3:CreateBucket`, `s3:ListBucket`, `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`
- `dynamodb:CreateTable`, `dynamodb:PutItem`, `dynamodb:Query`, `dynamodb:DeleteItem`

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Integration Tests

on: [push]

jobs:
  test:
    runs-on: ubuntu-latest
    env:
      AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
      AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
      AWS_REGION: us-east-1
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: 3.11
      - run: pip install -r requirements.txt
      - run: python setup_aws.py
      - run: pytest tests/test_integration.py -v
      - run: python cleanup_aws.py
```

## Best Practices

1. **Isolate Test Data**: Use unique prefixes for test objects (`integration_test/`, `e2e_test/`)
2. **Clean Up**: Always run `cleanup_aws.py` after testing to avoid costs
3. **Use Environment Variables**: Keep sensitive credentials in `.env` files (add to .gitignore)
4. **Parallel Testing**: Don't run integration tests in parallel (AWS rate limits)
5. **Monitor Costs**: Periodically check AWS Billing to catch unexpected charges

## Next Steps

1. Set up AWS credentials
2. Run `python setup_aws.py`
3. Run `pytest tests/test_integration.py -v`
4. Verify results
5. Run `python cleanup_aws.py` when done
