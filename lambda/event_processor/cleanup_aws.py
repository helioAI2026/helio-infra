import boto3
import sys
from botocore.exceptions import ClientError

def clean_s3_bucket(bucket_name):
    s3_client = boto3.client("s3")
    
    try:
        # List all objects
        response = s3_client.list_objects_v2(Bucket=bucket_name)
        
        if "Contents" not in response:
            print(f"✓ S3 bucket '{bucket_name}' is already empty")
            return True
        
        # Delete all objects
        for obj in response.get("Contents", []):
            s3_client.delete_object(Bucket=bucket_name, Key=obj["Key"])
            print(f"  Deleted: {obj['Key']}")
        
        # Delete bucket
        s3_client.delete_bucket(Bucket=bucket_name)
        print(f"✓ S3 bucket '{bucket_name}' deleted successfully")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchBucket":
            print(f"✓ S3 bucket '{bucket_name}' does not exist")
            return True
        else:
            print(f"✗ Error deleting S3 bucket: {e}")
            return False

def delete_dynamodb_table(table_name):
    dynamodb = boto3.client("dynamodb")
    
    try:
        dynamodb.delete_table(TableName=table_name)
        print(f"✓ DynamoDB table '{table_name}' deletion initiated")
        
        # Wait for table to be deleted
        waiter = dynamodb.get_waiter("table_not_exists")
        print("  Waiting for table to be deleted...")
        waiter.wait(TableName=table_name)
        print(f"  Table deleted successfully!")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"✓ DynamoDB table '{table_name}' does not exist")
            return True
        else:
            print(f"✗ Error deleting DynamoDB table: {e}")
            return False

def main():
    print("=" * 60)
    print("AWS Resources Cleanup for helioAI Event Processor")
    print("=" * 60)
    
    s3_bucket = "helio-ai-events"
    dynamodb_table = "HelioDriveEvents"
    
    # Confirm deletion
    print(f"\nThis will delete:")
    print(f"  - S3 bucket: {s3_bucket}")
    print(f"  - DynamoDB table: {dynamodb_table}")
    response = input("\nAre you sure? (yes/no): ").strip().lower()
    
    if response != "yes":
        print("Cleanup cancelled.")
        sys.exit(0)
    
    # Delete S3 bucket
    print(f"\n1. Cleaning S3 bucket ({s3_bucket})...")
    if not clean_s3_bucket(s3_bucket):
        print("✗ Cleanup failed")
        sys.exit(1)
    
    # Delete DynamoDB table
    print(f"\n2. Deleting DynamoDB table ({dynamodb_table})...")
    if not delete_dynamodb_table(dynamodb_table):
        print("✗ Cleanup failed")
        sys.exit(1)
    
    # Summary
    print("\n" + "=" * 60)
    print("Cleanup complete!")
    print("=" * 60)

if __name__ == "__main__":
    main()
