import boto3
import logging
import os
from botocore.exceptions import ClientError
from datetime import datetime, timezone
from pathlib import Path

class S3Adapter:
    
    def __init__(self, bucket_name):
        self.bucket_name = bucket_name
        self.s3_client = boto3.client('s3')
        self.logger = logging.getLogger(__name__)

    def upload_snapshot(self, file_path: str, driver_id: str, event_type: str = "drowsiness") -> str | None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = Path(file_path).name
        s3_key = f"snapshots/{driver_id}/{event_type}/{timestamp}_{filename}"

        return self._upload_file(file_path, s3_key, content_type="image/jpeg")
        
    def upload_video(self, file_path: str, driver_id: str, session_id: str) -> str | None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = Path(file_path).name
        s3_key = f"videos/{driver_id}/{session_id}/{timestamp}_{filename}"

        return self._upload_file(file_path, s3_key, content_type = "video/mp4")
    
    def upload_bytes(self, data: bytes, s3_key: str, content_type: str = "application/octet-stream") -> str | None:
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=data,
                ContentType=content_type,
            )
            self.logger.info("upload de bytes concluído: s3://%s/%s", self.bucket_name, s3_key)
            return s3_key
        except ClientError as e:
            self.logger.error("erro no upload de bytes para o s3: %s", e)
            return None
        
    def download_file(self, s3_key: str, destination_path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(destination_path), exist_ok=True)
            self.s3_client.download_file(self.bucket_name, s3_key, destination_path)
            self.logger.info("download concluído: %s -> %s", s3_key, destination_path)
            return True
        except ClientError as e:
            self.logger.error("erro ao baixar o arquivo do s3: %s", e)
            return False
    def generate_presigned_url(self, s3_key: str, expiration_seconds: int = 3600) -> str | None:
        try:
            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": s3_key},
                ExpiresIn=expiration_seconds,
            )
            return url
        except ClientError as e:
            self.logger.error("erro ao gerar a url pré assinada: %s", e)
            return None
        
    def list_events(self, driver_id: str, event_type: str | None = None) -> list[dict]:
        prefix = f"snapshots/{driver_id}/"
        if event_type:
            prefix += f"{event_type}/"

        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)

            results = []
            for page in pages:
                for obj in page.get("Contents", []):
                    results.append({
                        "key": obj["Key"],
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"].isoformat(),
                    })
            return results
        except ClientError as e:
            self.logger.error("erro ao listar objetos no s3: %s", e)
            return []

    def _upload_file(self, file_path: str, s3_key: str, content_type: str) -> str | None:
        try:
            self.s3_client.upload_file(
                file_path,
                self.bucket_name,
                s3_key,
                ExtraArgs={"ContentType": content_type},
            )
            self.logger.info("upload concluído: s3://%s/%s", self.bucket_name, s3_key)
            return s3_key
        except ClientError as e:
            self.logger.error("upload falhou: %s", e)
            return None
        except FileNotFoundError:
            self.logger.error("arquivo não encontrado: %s", file_path)
            return None