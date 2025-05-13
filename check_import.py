# check_import.py
import sys
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - CHECK_IMPORT - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

logger.info(f"Running import check...")
logger.info(f"Current Working Directory: {os.getcwd()}")
logger.info(f"Python sys.path: {sys.path}")

try:
    import signalrcore
    logger.info(f"Successfully imported 'signalrcore'. Location: {signalrcore.__file__}")
    # Try the specific import as well
    from signalrcore.asyncio.hub_connection_builder import HubConnectionBuilder
    logger.info("Successfully imported HubConnectionBuilder.")
    sys.exit(0) # Exit cleanly if imports work
except ImportError as e:
    logger.exception(f"ImportError encountered: {e}")
    sys.exit(1) # Exit with error code if import fails
except Exception as e:
    logger.exception(f"Non-ImportError during signalrcore import attempt: {e}")
    sys.exit(1) # Exit with error code