import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from listeners import register_listeners

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=False)
logging.basicConfig(level=logging.INFO)


def create_slack_app():
    app = App(token=os.environ.get("SLACK_BOT_TOKEN"))
    register_listeners(app)
    return app


if __name__ == "__main__":
    SocketModeHandler(create_slack_app(), os.environ.get("SLACK_APP_TOKEN")).start()
