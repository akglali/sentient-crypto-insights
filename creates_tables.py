from agent_core import get_db_connection

def setup_database():
    """Connects to the DB and creates the necessary tables if they don't exist."""
    print("Attempting to connect to the database to set up tables...")
    conn = get_db_connection()
    if conn is None:
        print("🔴 Could not proceed with table creation. Please check DB connection.")
        return
    
    #using cursor to excecute sql command
    cur = conn.cursor()


    try:
        # Create a table to store price history (a form of cache)
        # TEXT is fine for token_id, but NUMERIC is better for financial data than FLOAT
        # TIMESTAMPTZ stores the timestamp with time zone information
        cur.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id SERIAL PRIMARY KEY,
                token_id TEXT NOT NULL,
                price NUMERIC(20, 10) NOT NULL,
                market_cap NUMERIC(30, 10),
                volume_24h NUMERIC(30, 10),
                fetched_at TIMESTAMPTZ DEFAULT NOW()
            );
        """
        )

        cur.execute("""
    DROP TABLE IF EXISTS news_cache;
    CREATE TABLE news_cache (
        id SERIAL PRIMARY KEY,
        query TEXT NOT NULL,
        articles JSONB NOT NULL, -- Changed from 'headlines'
        fetched_at TIMESTAMPTZ DEFAULT NOW()
    );
"""
        )
        # Commit the transaction to make the changes permanent
        conn.commit()
        print("✅ Success! 'price_history' table created or already exists.")
        print("✅ Success! 'news_cache' table created or already exists.")
    except Exception as e:
        print(f"🔴 An error occurred: {e}")
        # Roll back the transaction in case of an error
        conn.rollback()
    finally:
        # Always close the cursor and connection
        cur.close()
        conn.close()

if __name__ == "__main__":
    setup_database()