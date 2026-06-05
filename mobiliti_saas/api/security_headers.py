"""
Middleware de seguridad HTTP para FastAPI.
Agrega headers de seguridad recomendados por OWASP.

Uso:
    from security_headers import SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)
"""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware que agrega headers de seguridad HTTP a todas las respuestas.
    
    Headers agregados:
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - Strict-Transport-Security (HSTS)
    - Content-Security-Policy
    - Referrer-Policy
    - Permissions-Policy
    - X-XSS-Protection
    """

    def __init__(self, app, report_only: bool = False):
        super().__init__(app)
        self.report_only = report_only

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        # Prevenir MIME sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevenir clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # HSTS: Forzar HTTPS durante 1 ano
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # CSP: Politica de seguridad de contenido
        # report_only=True envia reportes sin bloquear (para pruebas)
        csp_header = "Content-Security-Policy" if not self.report_only else "Content-Security-Policy-Report-Only"
        response.headers[csp_header] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self'; "
            "connect-src 'self' https://*.supabase.co; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )

        # Controlar informacion enviada en Referrer
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Controlar permisos del navegador
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), "
            "camera=(), "
            "geolocation=(), "
            "gyroscope=(), "
            "magnetometer=(), "
            "microphone=(), "
            "payment=(), "
            "usb=()"
        )

        # Proteccion XSS (legacy, modernos usan CSP)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Remover headers que revelan informacion del servidor
        # Nota: FastAPI/Starlette no agrega Server header por defecto
        # pero algunos proxies si. Esto es preventivo.
        if "Server" in response.headers:
            del response.headers["Server"]

        return response
