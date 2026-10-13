"""Load a folder of CSVs into one SQLite database with Spark (one CSV = one table).

Usage (from query-project/):
    uv run --with pyspark data/scripts/load_csv.py data/source_data/retail data/db/retail.db
"""

import argparse
import re
import sqlite3
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

SQLITE_JDBC = "org.xerial:sqlite-jdbc:3.49.1.0"  # downloaded by Spark on the first run


def clean_name(name):
    """'Order Date' -> 'order_date', so the LLM can write simple SQL without quotes."""
    return re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()


def sqlite_type(spark_type):
    """Map a Spark column type to a SQLite column type."""
    name = spark_type.simpleString()
    if name in ("int", "bigint", "smallint", "tinyint", "boolean"):
        return "INTEGER"
    if name in ("double", "float") or name.startswith("decimal"):
        return "REAL"
    return "TEXT"  # strings, and dates/timestamps (cast to text below)


def create_table(db_path, table, schema):
    """Spark 4's JDBC writer can't create SQLite tables, so create it here and let Spark append."""
    columns = ", ".join(f'"{f.name}" {sqlite_type(f.dataType)}' for f in schema.fields)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')  # re-running rebuilds instead of duplicating
        conn.execute(f'CREATE TABLE "{table}" ({columns})')
        conn.commit()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Load CSVs into a SQLite database with Spark")
    parser.add_argument("source_dir", type=Path, help="folder with .csv files")
    parser.add_argument("db_path", type=Path, help="output .db file")
    args = parser.parse_args()

    csv_files = sorted(args.source_dir.glob("*.csv"))
    if not csv_files:
        raise SystemExit(f"No .csv files in {args.source_dir}")
    args.db_path.parent.mkdir(parents=True, exist_ok=True)

    spark = (
        SparkSession.builder.appName("load_csv")
        .master("local[*]")
        .config("spark.jars.packages", SQLITE_JDBC)
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    url = f"jdbc:sqlite:{args.db_path.resolve()}"

    try:
        for csv_file in csv_files:
            df = (
                spark.read.option("header", True)
                .option("inferSchema", True)
                .option("multiLine", True)  # Kaggle text fields can contain newlines
                .option("escape", '"')      # standard CSV escapes quotes as ""
                .csv(str(csv_file))
            )
            df = df.toDF(*[clean_name(c) for c in df.columns])
            # SQLite has no date type; store '2024-01-05' as text, not as a millisecond number
            for field in df.schema.fields:
                if field.dataType.simpleString() in ("date", "timestamp", "timestamp_ntz"):
                    df = df.withColumn(field.name, F.col(field.name).cast("string"))
            table = clean_name(csv_file.stem)
            create_table(args.db_path, table, df.schema)

            # SQLite allows one writer at a time, so write from a single partition
            (
                df.coalesce(1).write.format("jdbc")
                .option("url", url)
                .option("driver", "org.sqlite.JDBC")
                .option("dbtable", table)
                .mode("append")  # the table was just created empty by create_table
                .save()
            )
            print(f"{table}: {df.count()} rows, columns = {df.columns}")
    finally:
        spark.stop()
    print(f"Done: {args.db_path}")


if __name__ == "__main__":
    main()
