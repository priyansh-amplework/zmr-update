import os
from pathlib import Path
from dotenv import load_dotenv
import psycopg2

load_dotenv(Path(__file__).resolve().parent / "zmr_brain" / ".env")
# Or fallback to root .env
load_dotenv(Path(__file__).resolve().parent / ".env")

try:
    from scripts.db_url import effective_database_url, ensure_ssl_for_managed
except ImportError:
    def effective_database_url():
        return (os.environ.get("RDS_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()

    def ensure_ssl_for_managed(url):
        return url


def main():
    db_url = effective_database_url()
    if not db_url:
        print("Neither RDS_DATABASE_URL nor DATABASE_URL found in environment")
        return
        
    c = psycopg2.connect(ensure_ssl_for_managed(db_url))
    cur = c.cursor()
    
    try:
        cur.execute("SELECT count(*) FROM documents_v2")
        total_docs_v2 = cur.fetchone()[0]
        print(f"Total documents_v2: {total_docs_v2}")
    except Exception as e:
        c.rollback()
        
    # Try documents_v2
    try:
        cur.execute("SELECT count(*) FROM documents_v2 WHERE source IN ('gmail', 'email_intake', 'email')")
        docs_email = cur.fetchone()[0]
        print(f"Emails in documents_v2: {docs_email}")
    except Exception as e:
        c.rollback()
        print(f"Could not check documents_v2: {e}")
        
    # Try documents
    try:
        cur.execute("SELECT count(*) FROM documents WHERE source IN ('gmail', 'email_intake', 'email')")
        docs_legacy = cur.fetchone()[0]
        print(f"Emails in legacy documents: {docs_legacy}")
    except Exception as e:
        c.rollback()
        
    # Try email_attachment_links
    try:
        cur.execute("SELECT count(*) FROM email_attachment_links")
        links = cur.fetchone()[0]
        print(f"Email attachment links: {links}")
    except Exception as e:
        c.rollback()
        
    # Check distinct emails in documents_v2 metadata
    try:
        cur.execute("SELECT count(*) FROM documents_v2 WHERE metadata ? 'email_message_id'")
        meta_emails = cur.fetchone()[0]
        print(f"Documents_v2 with email metadata: {meta_emails}")
    except Exception as e:
        c.rollback()

    c.close()

if __name__ == "__main__":
    main()
