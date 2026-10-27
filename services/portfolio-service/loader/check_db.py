"""Quick connection test: laptop -> port-forward -> PostgreSQL in Kubernetes."""
import psycopg

with psycopg.connect(host="localhost", port=15432, dbname="portfolio_db", user="postgres") as conn:
    db, version = conn.execute("SELECT current_database(), version()").fetchone()
    print("Connected to:", db)
    print("Server:", version.split(",")[0])

    tables = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    ).fetchall()
    print("Tables:", [t[0] for t in tables])