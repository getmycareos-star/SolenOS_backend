import sqlite3
import os

def update_notification_table():
    # Path to the SQLite database
    db_path = os.path.join(os.path.dirname(__file__), 'solenos.db')
    
    # Connect to the database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check current schema
    cursor.execute('PRAGMA table_info(notification)')
    columns = {column[1]: column[2] for column in cursor.fetchall()}
    
    # Check if recipient_person_id is the wrong type
    if columns.get('recipient_person_id') == 'INTEGER':
        print('Updating notification table schema...')
        # SQLite doesn't support altering column types directly, so we need to:
        # 1. Create a new table with the correct schema
        # 2. Copy data from the old table
        # 3. Drop the old table
        # 4. Rename the new table to the original name
        
        # Begin transaction
        cursor.execute('BEGIN TRANSACTION')
        
        # Create new table with correct schema
        cursor.execute('''
            CREATE TABLE notification_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_person_id TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                is_read BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                read_at DATETIME,
                archived_at DATETIME,
                related_entity_type TEXT,
                related_entity_id INTEGER,
                priority TEXT DEFAULT 'medium',
                FOREIGN KEY (recipient_person_id) REFERENCES person (id)
            );
        ''')
        
        # Copy data from old table to new table
        cursor.execute('''
            INSERT INTO notification_new (id, recipient_person_id, type, title, message, is_read, created_at, read_at, archived_at, related_entity_type, related_entity_id, priority)
            SELECT id, recipient_person_id, type, title, message, is_read, created_at, read_at, archived_at, related_entity_type, related_entity_id, priority
            FROM notification
        ''')
        
        # Drop old table
        cursor.execute('DROP TABLE notification')
        
        # Rename new table to original name
        cursor.execute('ALTER TABLE notification_new RENAME TO notification')
        
        # Recreate indexes
        cursor.execute('CREATE INDEX idx_notification_recipient_person_id ON notification (recipient_person_id);')
        cursor.execute('CREATE INDEX idx_notification_is_read ON notification (is_read);')
        cursor.execute('CREATE INDEX idx_notification_created_at ON notification (created_at);')
        
        conn.commit()
        print('Notification table schema updated successfully.')
    else:
        print('Notification table already exists with correct schema.')
    
    # Close the connection
    conn.close()

if __name__ == '__main__':
    update_notification_table()

