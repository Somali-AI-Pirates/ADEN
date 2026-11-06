"""설치된 서버 명령에서 앱 팩토리와 프로세스별 로그 설정을 연결한다."""

from copy import deepcopy

import uvicorn
from uvicorn.config import LOGGING_CONFIG


SERVER_LOG_CONFIG = deepcopy(LOGGING_CONFIG)
SERVER_LOG_CONFIG["formatters"]["application"] = {
    "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
}
SERVER_LOG_CONFIG["handlers"]["application"] = {
    "class": "logging.StreamHandler",
    "formatter": "application",
    "stream": "ext://sys.stderr",
}
SERVER_LOG_CONFIG["root"] = {"handlers": ["application"], "level": "WARNING"}
SERVER_LOG_CONFIG["loggers"]["app"] = {
    "handlers": ["application"],
    "level": "INFO",
    "propagate": False,
}


def server() -> None:
    uvicorn.run(
        "app.api.app:create_app",
        factory=True,
        reload=False,
        log_config=SERVER_LOG_CONFIG,
    )


def dev() -> None:
    uvicorn.run(
        "app.api.app:create_app",
        factory=True,
        reload=True,
        log_config=SERVER_LOG_CONFIG,
    )
