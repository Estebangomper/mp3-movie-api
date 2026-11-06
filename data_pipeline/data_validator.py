# src/data_validator.py
import logging
import pandas as pd


logger = logging.getLogger(__name__)


def validate_dataframe(df, required_columns, numeric_columns):
    """Validate the DataFrame and return valid data.

    required_columns: a list of column names that must exist.
    numeric_columns: a list of column names whose values should be numeric.
    """
    if not isinstance(df, pd.DataFrame):
        logger.error("Input data must be a table (CSV file)")
        raise ValueError("Input data must be a table (CSV file)")

    for col in required_columns:
        if col not in df.columns:
            logger.error(f"Missing required column: {col}")
            raise ValueError(f"Missing required column: {col}")

    before = len(df)

    for col in numeric_columns:
        invalid_rows = []
        for i, value in df[col].items():
            if pd.notna(value):
                try:
                    float(value)
                except ValueError:
                    logger.warning(f"Invalid numeric value in {col}: {value}")
                    invalid_rows.append(i)

        df = df.drop(invalid_rows)

        df[col] = pd.to_numeric(df[col])

    logger.debug(f"Validation: {before} -> {len(df)} rows")
    return df
