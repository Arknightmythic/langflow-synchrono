from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class DispatchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_id: str = Field(..., alias="fileId", description="Target file identifier in manual_matches")
    master_parquet_path: Optional[str] = Field(None, alias="masterParquetPath", description="Custom master Parquet path (local or s3://)")
    llm_model: Optional[str] = Field("gemma3:12b", alias="llmModel", description="Ollama LLM model name")
    dry_run: Optional[bool] = Field(False, alias="dryRun", description="Simulate without writing back to manual_matches")
    clear_cache: Optional[bool] = Field(False, alias="clearCache", description="Clear cached reasoning patterns before running")
    limit: Optional[int] = Field(None, description="Max rows to process")


class DispatchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str
    jobId: str
    fileId: str
    message: str


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    found: bool
    status: str
    jobId: Optional[str] = None
    fileId: Optional[str] = None
    done: bool = False
    stage: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    queuedAt: Optional[str] = None
    heartbeatAt: Optional[str] = None
    updatedAt: Optional[str] = None
    message: Optional[str] = None


class ClearCacheResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str
    deleted_patterns: int
    message: str


class HealthResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str
    database: str
    llm_model: str
    master_parquet_path: str


class RunSyncResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str
    fileId: str
    total_rows: int = 0
    patterns_generated: int = 0
    cache_hits: int = 0
    llm_hits: int = 0
    llm_calls: int = 0
    master_source: str
    duration_seconds: float
    dry_run: bool = False
