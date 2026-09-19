class ServiceError(Exception):
    """Stable client-safe error. Never include provider payloads or credentials."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
