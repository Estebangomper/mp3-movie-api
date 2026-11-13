import io
import logging
from pathlib import Path
import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from src import HealthResponse, MovieCache, ProcessConfig, log_requests, setup_logging
from src import (
    MovieStorage,
    MovieUploadInfo,
    MovieDataResponse,
    MovieProcessingReport,
)
from src.storage import MOVIE_COLUMNS
from data_pipeline import create_cleaning_report, process_data, validate_dataframe

setup_logging(verbose=True)
logger = logging.getLogger(__name__)

app = FastAPI(title="Movie API", version="1.0.0")
storage = MovieStorage()
cache = MovieCache()
app.middleware("http")(log_requests)

@app.get("/health", response_model=HealthResponse)
def health_check():
    """Return the application status and version."""
    return {"status": "ok", "version": "1.0.0"}

@app.post("/upload", response_model=MovieUploadInfo)
async def upload_movies(file: UploadFile = File(...)):
    """Read a movie CSV and replace the stored collection."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".csv":
        logger.warning(f"Unsupported file format: {suffix}")
        raise HTTPException(status_code=400, detail=f"Unsupported file format: {suffix}. Please upload a .csv file.")

    contents = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(contents))
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as error:
        logger.warning(f"Could not read CSV file {file.filename}: {error}")
        raise HTTPException(status_code=400, detail=f"Could not read CSV file: {error}")

    if len(df.columns) != len(MOVIE_COLUMNS):
        logger.warning(f"Expected columns {MOVIE_COLUMNS}, got {list(df.columns)}")
        raise HTTPException(status_code=400, detail=f"Expected exactly these columns: {MOVIE_COLUMNS}")
    for column in MOVIE_COLUMNS:
        if column not in df.columns:
            logger.warning(f"Missing movie column: {column}")
            raise HTTPException(status_code=400, detail=f"Missing movie column: {column}")
    if df.empty:
        logger.warning(f"CSV file has no data rows: {file.filename}")
        raise HTTPException(status_code=400, detail="CSV file has no data rows")
    df = df[MOVIE_COLUMNS]

    storage.save(df)
    cache.invalidate()
    logger.info(f"Uploaded movies: filename={file.filename}, rows={len(df)}")
    return {
        "filename": file.filename,
        "rows": len(df),
        "columns": len(df.columns),
        "column_names": list(df.columns),
    }

@app.get("/movies", response_model=MovieDataResponse)
def get_movies(limit: int = Query(default=100, ge=1)):
    """Return up to limit stored movies and the total count, using the cache."""
    cached = cache.get(limit)
    if cached is not None:
        logger.info(f"Returned cached movies: limit={limit}")
        return cached

    records = storage.select_columns(MOVIE_COLUMNS, limit)
    total_rows = storage.count_movies()
    response = {"data": records, "total_rows": total_rows}

    cache.set(limit, response)
    logger.info(f"Retrieved movies: returned_rows={len(records)}")
    return response

@app.get("/movies/search", response_model=list[dict])
def query_movies(
    columns: list[str] = Query(default=["title"]),
    min_rating: float = Query(default=0, ge=0, le=10),
    limit: int = Query(default=100, ge=1)
):
    """Return selected columns of movies with rating >= min_rating."""
    try:
        records = storage.query_movies(columns, min_rating, limit)
    except ValueError as error:
        logger.warning(f"Could not query movies: {error}")
        raise HTTPException(status_code=400, detail=str(error))

    logger.info(f"Queried movies: columns={columns}, min_rating={min_rating}, returned_rows={len(records)}")
    return records

@app.post("/movies/process", response_model=MovieProcessingReport)
def process_movies(config: ProcessConfig):
    """Validate and process the stored movies using MP1 functions."""
    df = storage.get()
    if df.empty:
        logger.warning("No movies stored to process")
        raise HTTPException(status_code=400, detail="No movies stored. Upload a CSV first.")

    config_dict = config.model_dump()
    required_columns = config_dict["validation"]["required_columns"]
    numeric_columns = config_dict["validation"]["numeric_columns"]

    for col in numeric_columns:
        if col not in df.columns:
            logger.warning(f"Numeric column not found: {col}")
            raise HTTPException(status_code=400, detail=f"Numeric column not found: {col}")

    try:
        before = len(df)
        validated_df = validate_dataframe(df, required_columns, numeric_columns)
        validation_rows_removed = before - len(validated_df)
        logger.info(f"Validated movies: validation_removed={validation_rows_removed}")

        df_before = validated_df.copy()
        processed_df = process_data(validated_df, config_dict)
        cleaning_report = create_cleaning_report(df_before, processed_df)
    except ValueError as error:
        logger.warning(f"Could not process movies: {error}")
        raise HTTPException(status_code=400, detail=str(error))

    for column in MOVIE_COLUMNS:
        if column not in processed_df.columns:
            logger.warning(f"Processing removed movie column: {column}")
            raise HTTPException(status_code=400, detail=f"Processing removed movie column: {column}. Use missing axis 'rows'.")

    storage.save(processed_df)
    cache.invalidate()
    logger.info(f"Processed movies: validation_removed={validation_rows_removed}, rows_before={len(df_before)}, rows_after={len(processed_df)}")
    return {
        "validation_rows_removed": validation_rows_removed,
        "cleaning_report": cleaning_report,
    }