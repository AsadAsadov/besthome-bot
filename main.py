from config import ENV
from core.logging import logger
import besthome_unified_bot


def main():
    logger.info("BestHome bot starting env=%s", ENV)
    besthome_unified_bot.start_bot_server()


if __name__ == "__main__":
    main()
