import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

SECRET_KEY = os.getenv('JWT_SECRET', 'change-me-in-production-32-char-minimum-secret-key')
ALGORITHM = 'HS256'
TOKEN_EXPIRY_HOURS = int(os.getenv('JWT_EXPIRY_HOURS', '24'))

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def create_token(user_id: int, email: str, role: str, company_id: Optional[int]) -> str:
    payload = {
        'sub': str(user_id),
        'email': email,
        'role': role,
        'company_id': company_id,
        'exp': datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail='Token expired')
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail='Invalid token')


class CurrentUser:
    def __init__(self, user_id: int, email: str, role: str, company_id: Optional[int]):
        self.id = user_id
        self.email = email
        self.role = role
        self.company_id = company_id

    @property
    def is_platform_admin(self) -> bool:
        return self.role == 'platform_admin'

    def effective_company(self, ctx: Optional[int] = None) -> Optional[int]:
        if self.is_platform_admin and ctx:
            return ctx
        return self.company_id

    def require_company(self, ctx: Optional[int] = None) -> int:
        cid = self.effective_company(ctx)
        if not cid:
            raise HTTPException(status_code=400, detail='Company context required')
        return cid

    def can_manage_company(self) -> bool:
        return self.role in ('platform_admin', 'company_admin')

    def can_approve(self) -> bool:
        return self.role in ('platform_admin', 'company_admin', 'hr')

    def can_view_all_employees(self) -> bool:
        return self.role in ('platform_admin', 'company_admin', 'hr', 'manager')


async def get_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> CurrentUser:
    if not creds:
        raise HTTPException(status_code=401, detail='Authentication required')
    data = decode_token(creds.credentials)
    return CurrentUser(int(data['sub']), data['email'], data['role'], data.get('company_id'))


def roles(*allowed: str):
    """Dependency factory that enforces role membership."""
    async def dep(user: CurrentUser = Depends(get_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail='Forbidden')
        return user
    return dep
