import json
import logging
import os
import base64
import tempfile
from typing import Any, Dict
from botocore.exceptions import ClientError
from s3_adapter import S3Adapter
from dynamodb_adapter import DynamoDBAdapter

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.getenv("S3_BUCKET", "helio-ai-events")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE", "HelioDriveEvents")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


class EventProcessor:
    """Processa eventos de detecção de sonolência"""
    
    def __init__(self):
        self.s3_adapter = S3Adapter(S3_BUCKET)
        self.dynamodb_adapter = DynamoDBAdapter(DYNAMODB_TABLE, AWS_REGION)
    
    def process_drowsiness_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processa evento de detecção de sonolência com snapshot
        
        Esperado:
        {
            "action": "save_event",
            "driver_id": "driver123",
            "event_type": "drowsiness",
            "ear_value": 0.25,
            "snapshot_base64": "iVBORw0KGgo..." (opcional),
            "session_id": "session_001" (opcional)
        }
        """
        try:
            driver_id = event_data.get("driver_id")
            event_type = event_data.get("event_type", "drowsiness")
            ear_value = float(event_data.get("ear_value", 0.0))
            session_id = event_data.get("session_id")
            snapshot_base64 = event_data.get("snapshot_base64")
            
            if not driver_id:
                return self._error_response(400, "driver_id é obrigatório")
            
            if not (0 <= ear_value <= 1):
                return self._error_response(400, "ear_value deve estar entre 0 e 1")
            
            s3_key = None
            
            if snapshot_base64:
                try:
                    snapshot_bytes = base64.b64decode(snapshot_base64)
                    s3_key = self.s3_adapter.upload_bytes(
                        snapshot_bytes,
                        f"snapshots/{driver_id}/{event_type}/{driver_id}_snapshot.jpg",
                        content_type="image/jpeg"
                    )
                except Exception as e:
                    logger.error(f"Erro ao upload de snapshot: {e}")
                    return self._error_response(500, f"Falha no upload: {str(e)}")
            
            event_id = self.dynamodb_adapter.save_event(
                driver_id=driver_id,
                event_type=event_type,
                ear_value=ear_value,
                s3_key=s3_key,
                session_id=session_id
            )
            
            if not event_id:
                return self._error_response(500, "Falha ao salvar evento no DynamoDB")
            
            logger.info(f"Evento salvo: event_id={event_id}, driver={driver_id}")
            
            return self._success_response({
                "event_id": event_id,
                "driver_id": driver_id,
                "s3_key": s3_key,
                "message": "Evento salvo com sucesso"
            })
        
        except Exception as e:
            logger.error(f"Erro ao processar evento de sonolência: {e}")
            return self._error_response(500, str(e))
    
    def process_session_summary(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processa resumo de sessão
        
        Esperado:
        {
            "action": "save_session_summary",
            "driver_id": "driver123",
            "session_id": "session_001",
            "total_events": 5,
            "duration_seconds": 1800,
            "video_base64": "..." (opcional)
        }
        """
        try:
            driver_id = event_data.get("driver_id")
            session_id = event_data.get("session_id")
            total_events = int(event_data.get("total_events", 0))
            duration_seconds = int(event_data.get("duration_seconds", 0))
            video_base64 = event_data.get("video_base64")
            
            if not driver_id or not session_id:
                return self._error_response(400, "driver_id e session_id são obrigatórios")
            
            s3_video_key = None
            
            if video_base64:
                try:
                    video_bytes = base64.b64decode(video_base64)
                    s3_video_key = self.s3_adapter.upload_bytes(
                        video_bytes,
                        f"videos/{driver_id}/{session_id}/session_video.mp4",
                        content_type="video/mp4"
                    )
                except Exception as e:
                    logger.error(f"Erro ao upload de vídeo: {e}")
                    return self._error_response(500, f"Falha no upload de vídeo: {str(e)}")
            
            success = self.dynamodb_adapter.save_session_summary(
                driver_id=driver_id,
                session_id=session_id,
                total_events=total_events,
                duration_seconds=duration_seconds,
                s3_video_key=s3_video_key
            )
            
            if not success:
                return self._error_response(500, "Falha ao salvar resumo da sessão")
            
            logger.info(f"Resumo de sessão salvo: session_id={session_id}, driver={driver_id}")
            
            return self._success_response({
                "driver_id": driver_id,
                "session_id": session_id,
                "total_events": total_events,
                "duration_seconds": duration_seconds,
                "s3_video_key": s3_video_key,
                "message": "Resumo de sessão salvo com sucesso"
            })
        
        except Exception as e:
            logger.error(f"Erro ao processar resumo de sessão: {e}")
            return self._error_response(500, str(e))
    
    def get_driver_events(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recupera eventos de um motorista
        
        Esperado:
        {
            "action": "get_events",
            "driver_id": "driver123",
            "event_type": "drowsiness" (opcional),
            "limit": 50
        }
        """
        try:
            driver_id = event_data.get("driver_id")
            event_type = event_data.get("event_type")
            limit = int(event_data.get("limit", 50))
            
            if not driver_id:
                return self._error_response(400, "driver_id é obrigatório")
            
            events = self.dynamodb_adapter.get_events_by_driver(
                driver_id=driver_id,
                event_type=event_type,
                limit=limit
            )
            
            logger.info(f"Eventos recuperados: driver={driver_id}, count={len(events)}")
            
            return self._success_response({
                "driver_id": driver_id,
                "event_type": event_type,
                "total_events": len(events),
                "events": events
            })
        
        except Exception as e:
            logger.error(f"Erro ao recuperar eventos: {e}")
            return self._error_response(500, str(e))
    
    def get_presigned_url(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Gera URL pré-assinada para download
        
        Esperado:
        {
            "action": "get_presigned_url",
            "s3_key": "snapshots/driver123/drowsiness/file.jpg",
            "expiration_seconds": 3600 (opcional)
        }
        """
        try:
            s3_key = event_data.get("s3_key")
            expiration_seconds = int(event_data.get("expiration_seconds", 3600))
            
            if not s3_key:
                return self._error_response(400, "s3_key é obrigatório")
            
            url = self.s3_adapter.generate_presigned_url(s3_key, expiration_seconds)
            
            if not url:
                return self._error_response(500, "Falha ao gerar URL pré-assinada")
            
            logger.info(f"URL pré-assinada gerada: s3_key={s3_key}")
            
            return self._success_response({
                "s3_key": s3_key,
                "presigned_url": url,
                "expiration_seconds": expiration_seconds
            })
        
        except Exception as e:
            logger.error(f"Erro ao gerar URL pré-assinada: {e}")
            return self._error_response(500, str(e))
    
    @staticmethod
    def _success_response(data: Dict[str, Any]) -> Dict[str, Any]:
        """Formata resposta de sucesso"""
        return {
            "statusCode": 200,
            "body": json.dumps(data),
            "headers": {"Content-Type": "application/json"}
        }
    
    @staticmethod
    def _error_response(status_code: int, message: str) -> Dict[str, Any]:
        """Formata resposta de erro"""
        return {
            "statusCode": status_code,
            "body": json.dumps({"error": message}),
            "headers": {"Content-Type": "application/json"}
        }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Função Lambda principal que processa eventos de detecção de sonolência
    
    Event esperado (no body):
    {
        "action": "save_event" | "save_session_summary" | "get_events" | "get_presigned_url",
        ... outros campos específicos da ação
    }
    """
    try:
        logger.info(f"Evento recebido: {json.dumps(event)}")
        
        body = event.get("body")
        if isinstance(body, str):
            event_data = json.loads(body)
        else:
            event_data = event
        
        processor = EventProcessor()
        
        action = event_data.get("action")
        
        if action == "save_event":
            return processor.process_drowsiness_event(event_data)
        
        elif action == "save_session_summary":
            return processor.process_session_summary(event_data)
        
        elif action == "get_events":
            return processor.get_driver_events(event_data)
        
        elif action == "get_presigned_url":
            return processor.get_presigned_url(event_data)
        
        else:
            logger.error(f"Ação inválida: {action}")
            return processor._error_response(
                400,
                f"Ação inválida. Ações suportadas: save_event, save_session_summary, get_events, get_presigned_url"
            )
    
    except json.JSONDecodeError as e:
        logger.error(f"Erro ao fazer parse do JSON: {e}")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "JSON inválido"}),
            "headers": {"Content-Type": "application/json"}
        }
    
    except Exception as e:
        logger.error(f"Erro inesperado: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Erro interno do servidor: {str(e)}"}),
            "headers": {"Content-Type": "application/json"}
        }
