import os
import unicodedata

import boto3


def _ascii_subject(text: str) -> str:
    """O assunto do SNS para e-mail deve ser ASCII e ter menos de 100 caracteres."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")[:99]


def publish(subject: str, message: str) -> None:
    boto3.client("sns").publish(
        TopicArn=os.environ["ALERT_TOPIC_ARN"],
        Subject=_ascii_subject(subject),
        Message=message,
    )
