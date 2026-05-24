import boto3
import sys
from botocore.exceptions import ClientError

def create_s3_bucket(bucket_name, region="us-east-1"):
    s3_client = boto3.client("s3", region_name=region)
    
    try:
        if region == "us-east-1":
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region}
            )
        print(f"✓ S3 bucket '{bucket_name}' created successfully")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "BucketAlreadyOwnedByYou":
            print(f"✓ S3 bucket '{bucket_name}' already exists")
            return True
        else:
            print(f"✗ Error creating S3 bucket: {e}")
            return False

def create_dynamodb_table(table_name, region="us-east-1"):
    dynamodb = boto3.client("dynamodb", region_name=region)
    
    try:
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "driver_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"}
            ],
            AttributeDefinitions=[
                {"AttributeName": "driver_id", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST"
        )
        print(f"✓ DynamoDB table '{table_name}' created successfully")
        
        # Wait for table to be created
        waiter = dynamodb.get_waiter("table_exists")
        print("  Waiting for table to be ready...")
        waiter.wait(TableName=table_name)
        print(f"  Table is ready!")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"✓ DynamoDB table '{table_name}' already exists")
            return True
        else:
            print(f"✗ Error creating DynamoDB table: {e}")
            return False

def verify_aws_credentials():
    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        print(f"✓ AWS credentials verified")
        print(f"  Account ID: {identity['Account']}")
        print(f"  User ARN: {identity['Arn']}")
        return True
    except ClientError as e:
        print(f"✗ AWS credentials not configured: {e}")
        return False

def main():
    print("=" * 60)
    print("AWS Resources Setup for helioAI Event Processor")
    print("=" * 60)
    
    region = "us-east-1"
    s3_bucket = "helio-ai-events"
    dynamodb_table = "HelioDriveEvents"
    
    # Verify credentials
    print("\n1. Verifying AWS credentials...")
    if not verify_aws_credentials():
        print("\n✗ Setup failed: AWS credentials not configured")
        print("  Please set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")
        sys.exit(1)
    
    # Create S3 bucket
    print(f"\n2. Creating S3 bucket ({s3_bucket})...")
    if not create_s3_bucket(s3_bucket, region):
        sys.exit(1)
    
    # Create DynamoDB table
    print(f"\n3. Creating DynamoDB table ({dynamodb_table})...")
    if not create_dynamodb_table(dynamodb_table, region):
        sys.exit(1)
    
    # Summary
    print("\n" + "=" * 60)
    print("Setup complete! Resources are ready for testing:")
    print(f"  S3 Bucket: {s3_bucket}")
    print(f"  DynamoDB Table: {dynamodb_table}")
    print(f"  Region: {region}")
    print("\nEnvironment variables for testing:")
    print(f"  S3_BUCKET={s3_bucket}")
    print(f"  DYNAMODB_TABLE={dynamodb_table}")
    print(f"  AWS_REGION={region}")
    print("=" * 60)

if __name__ == "__main__":
    main()
