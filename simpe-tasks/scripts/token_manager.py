"""일회용 토큰 생성/검증.

pick이 토큰을 발급하고, complete/reject가 검증한다.
상태 파일에는 해시만 저장하므로 모델이 JSON을 읽어도 원본 토큰을 알 수 없다.
"""

import hashlib
import secrets


def generate():
    """URL-safe 일회용 토큰 생성 (16자)."""
    return secrets.token_urlsafe(12)


def hash_token(token):
    """토큰의 SHA-256 해시 앞 32자를 반환한다 (파일 저장용)."""
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def validate(provided, stored_hash):
    """제공된 토큰이 저장된 해시와 일치하는지 확인한다."""
    return hash_token(provided) == stored_hash
