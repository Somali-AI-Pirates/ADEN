import logging

from app.api.app import create_app

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("app").setLevel(logging.INFO)

app = create_app()
