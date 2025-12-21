import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import ENV
from core.logging import logger
import traceback

import besthome_unified_bot


def main():
    logger.info("BestHome bot starting env=%s", ENV)
    besthome_unified_bot.main()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("FATAL ERROR (full traceback):")
        print(traceback.format_exc())
        raise
