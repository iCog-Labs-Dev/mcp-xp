import hashlib
import shutil
import asyncio
import pathlib
import logging

from enum import Enum


logger = logging.getLogger("Invocation")


# NOTE: Here are the list of tools used by bioblend to index invocation dataset outputs for visualiztion via JBrowse.
class FASTAIndexerTools(Enum):
    INDEXER_TOOL = "CONVERTER_fasta_to_fai"

class VCFIndexerTools(Enum):
    COMPRESSER_TOOL = "CONVERTER_uncompressed_to_gz"
    INDEXER_TOOL = "CONVERTER_vcf_bgzip_to_tabix_0"

class BAMIndexerTools(Enum):
    INDEXER_TOOL = "CONVERTER_Bam_Bai_0"

class GTFIndexerTools(Enum):
    COMPRESSER_TOOL = "CONVERTER_uncompressed_to_gz"
    INDEXER_TOOL = "CONVERTER_interval_to_tabix_0"
    

def generate_request_hash(*args) -> str:
    """Generate hash for request deduplication"""
    request_string = "|".join(str(arg) if arg is not None else "None" for arg in args)
    return hashlib.md5(request_string.encode()).hexdigest()

def _rmtree_sync(path: pathlib.Path):
    shutil.rmtree(path, ignore_errors=True)
    
def log_task_error(task: asyncio.Task, *, task_name: str) -> None:
    """Log errors from completed background tasks."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        logger.info(f"Background task '{task_name}' was cancelled.")
        return
    
    if exc is not None:
        logger.error(
            f"Background task '{task_name}' failed",
            exc_info=exc,
        )