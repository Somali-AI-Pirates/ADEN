"""FastAPI CLI가 불러오는 서버 앱과 콘솔 로그 설정."""

import logging

from app.api.app import create_app


logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("app").setLevel(logging.INFO)
# 요청 요약은 앱 미들웨어가 라우트 경로와 요청 ID로 기록한다.
logging.getLogger("uvicorn.access").disabled = True

app = create_app()
