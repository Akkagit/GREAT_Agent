"""Safe errors exposed by the research HTTP and worker boundaries."""


class ResearchError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = list(details or [])
